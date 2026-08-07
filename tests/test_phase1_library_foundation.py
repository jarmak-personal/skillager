from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from uuid import uuid4

from skillager.catalog.impl import add_collection, load_collections, register_library_collection, remove_collection
from skillager.commands.context import catalog_root, root
from skillager.library.model import LibraryIdentity, LibraryLayout, LibraryRegistration, normalize_skill_name
from skillager.library.paths import load_library_registration
from skillager.state.locking import ResourceLockTimeout, resource_lock
from skillager.state.statefiles import read_user_json, write_user_json
from skillager.state.trust import load_trust


class SkillagerResourceLockTests(unittest.TestCase):

    def test_held_cross_process_lock_times_out_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resource = Path(tmp) / "state" / "trust.json"
            script = """
import sys
from pathlib import Path
from skillager.state.locking import resource_lock

with resource_lock(Path(sys.argv[1]), timeout=2.0):
    print("locked", flush=True)
    sys.stdin.read(1)
"""
            holder = subprocess.Popen(
                [sys.executable, "-c", script, str(resource)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                assert holder.stdout is not None
                self.assertEqual(holder.stdout.readline().strip(), "locked")
                with self.assertRaises(ResourceLockTimeout) as raised:
                    with resource_lock(resource, timeout=0.05):
                        pass
                self.assertIn(str(resource.resolve()), str(raised.exception))
            finally:
                if holder.stdin is not None:
                    holder.stdin.write("x")
                    holder.stdin.flush()
                _, stderr = holder.communicate(timeout=5)
                self.assertEqual(holder.returncode, 0, stderr)

    def test_concurrent_trust_mutators_do_not_lose_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "state"
            script = """
import sys
from pathlib import Path
from skillager.state.trust import set_trust

set_trust(Path(sys.argv[1]), sys.argv[2], "reviewed", sys.argv[3], {"type": "project"})
"""
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", script, str(state_root), f"project/skill-{index}", f"hash-{index}"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for index in range(8)
            ]
            for process in processes:
                _, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(
                sorted(load_trust(state_root).get("skills", {})),
                [f"project/skill-{index}" for index in range(8)],
            )

    def test_reverse_order_multi_resource_requests_complete_without_deadlock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"
            script = """
import sys
import time
from pathlib import Path
from skillager.state.locking import resource_locks

with resource_locks([Path(sys.argv[1]), Path(sys.argv[2])], timeout=2.0):
    time.sleep(0.1)
"""
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", script, str(first), str(second)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ),
                subprocess.Popen(
                    [sys.executable, "-c", script, str(second), str(first)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ),
            ]
            for process in processes:
                _, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)

    def test_symlinked_lock_directory_is_refused(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            resource = root_path / "state" / "trust.json"
            resource.parent.mkdir()
            outside = root_path / "outside"
            outside.mkdir()
            (resource.parent / ".skillager-locks").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "unsafe Skillager lock directory"):
                with resource_lock(resource):
                    pass


class SkillagerLibraryModelTests(unittest.TestCase):

    def test_library_identity_and_registration_validate_schema_and_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = LibraryLayout.from_root(Path(tmp) / "library")
            layout.skills.mkdir(parents=True)
            library_id = str(uuid4())
            identity = LibraryIdentity.from_mapping(
                {
                    "schema": "skillager.library.v1",
                    "library_id": library_id,
                    "namespace": "lib",
                    "created_at": "2026-08-06T00:00:00Z",
                    "git": {"mode": "system"},
                }
            )
            self.assertEqual(identity.library_id, library_id)
            registration = LibraryRegistration(library_id=library_id, layout=layout)
            self.assertEqual(LibraryRegistration.from_mapping(registration.to_mapping()), registration)

            bad_path = registration.to_mapping()
            bad_path["path"] = str(layout.root / "elsewhere")
            with self.assertRaisesRegex(ValueError, "<library_root>/skills"):
                LibraryRegistration.from_mapping(bad_path)

    def test_skill_names_are_flat_and_skill_paths_cannot_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = LibraryLayout.from_root(Path(tmp) / "library")
            layout.skills.mkdir(parents=True)
            self.assertEqual(normalize_skill_name("lib/Pandas 2"), "pandas-2")
            self.assertEqual(layout.skill_root("Pandas 2"), layout.skills / "pandas-2")
            for unsafe in ("../escape", "nested/escape", "nested\\escape", "--"):
                with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                    layout.skill_root(unsafe)

    def test_symlinked_skill_destination_cannot_escape_library(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            layout = LibraryLayout.from_root(root_path / "library")
            layout.skills.mkdir(parents=True)
            outside = root_path / "outside"
            outside.mkdir()
            (layout.skills / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "escapes the library"):
                layout.skill_root("escape")


class SkillagerReservedLibraryCollectionTests(unittest.TestCase):

    def test_reserved_library_registration_is_idempotent_and_protected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            catalog = root_path / "catalog"
            library = root_path / "library"
            (library / "skills").mkdir(parents=True)
            library_id = str(uuid4())

            first = register_library_collection(catalog, library, library_id)
            second = register_library_collection(catalog, library, library_id)
            self.assertEqual(first, second)
            self.assertEqual(load_library_registration(catalog).library_id, library_id)  # type: ignore[union-attr]
            self.assertEqual(load_collections(catalog)["collections"]["lib"]["kind"], "library")

            with self.assertRaisesRegex(ValueError, "different personal skill library"):
                register_library_collection(catalog, library, str(uuid4()))
            with self.assertRaisesRegex(ValueError, "cannot be removed"):
                remove_collection(catalog, "lib")
            with self.assertRaisesRegex(ValueError, "reserved"):
                add_collection(catalog, "lib", library)

    def test_legacy_normal_collection_named_lib_can_be_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            catalog = root_path / "catalog"
            legacy = root_path / "legacy"
            legacy.mkdir()
            write_user_json(
                catalog / "collections.json",
                {"collections": {"lib": {"name": "lib", "path": str(legacy)}}},
            )
            self.assertTrue(remove_collection(catalog, "lib"))
            self.assertEqual(read_user_json(catalog / "collections.json", {})["collections"], {})

    def test_registration_refuses_symlinked_skills_directory(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            library = root_path / "library"
            outside = root_path / "outside"
            library.mkdir()
            outside.mkdir()
            (library / "skills").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "non-symlinked directory"):
                register_library_collection(root_path / "catalog", library, str(uuid4()))

    def test_concurrent_collection_registrations_do_not_lose_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            catalog = root_path / "catalog"
            collections = []
            for index in range(6):
                collection = root_path / f"collection-{index}"
                collection.mkdir()
                collections.append(collection)
            script = """
import sys
from pathlib import Path
from skillager.catalog.impl import add_collection

add_collection(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
"""
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", script, str(catalog), f"collection-{index}", str(collection)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for index, collection in enumerate(collections)
            ]
            for process in processes:
                _, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(
                sorted(load_collections(catalog)["collections"]),
                [f"collection-{index}" for index in range(6)],
            )


class SkillagerCommandContextTests(unittest.TestCase):

    def test_explicit_state_and_catalog_roots_are_resolved_and_cached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            args = Namespace(state_dir=root_path / "state" / ".." / "state", catalog_state_dir=root_path / "catalog" / ".." / "catalog")
            self.assertEqual(root(args), (root_path / "state").resolve())
            self.assertEqual(catalog_root(args), (root_path / "catalog").resolve())
            self.assertEqual(root(args), getattr(args, "_skillager_state_root"))
