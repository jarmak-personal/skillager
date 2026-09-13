from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from skillager.catalog.impl import _load_collection_index, _load_or_refresh_collection_index, refresh_collection
from skillager.catalog.storage import cache_path, write_collection
from skillager.library.service import initialize_library, new_library_skill
from skillager.skills.search import search
from skillager.state import approvals, database
from skillager.state.trust import clear_trust, content_hash, load_trust, set_trust, trust_info, unblock_trust


class SqliteApprovalTests(unittest.TestCase):
    def test_legacy_read_is_pure_and_migration_preserves_every_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = {"state": "pinned", "content_hash": "old", "source": {"type": "project"}, "custom": [1, 2]}
            data = {
                "skills": {"project/old": {"state": "blocked", "content_hash": "old", "previous_approval": previous}},
                "global_approvals": {"source#name": {"state": "reviewed", "content_hash": "global", "lint_override": {"reason": "audited", "at": "then", "findings": []}}},
                "extension": {"keep": True},
            }
            legacy = root / "trust.json"
            legacy.write_text(json.dumps(data))
            original = legacy.read_bytes()
            self.assertEqual(load_trust(root), data)
            with approvals.snapshot([root]), patch.object(approvals, "read_user_json", side_effect=AssertionError("repeated JSON decode")):
                for _ in range(20):
                    self.assertEqual(trust_info(root, "project/old", "old")["state"], "blocked")
            self.assertFalse(approvals.database_path(root).exists())
            set_trust(root, "project/new", "reviewed", "new", {"type": "project"})
            migrated = load_trust(root)
            self.assertEqual(migrated["skills"]["project/old"], data["skills"]["project/old"])
            self.assertEqual(migrated["global_approvals"], data["global_approvals"])
            self.assertEqual(migrated["extension"], data["extension"])
            self.assertEqual((root / "trust.json.migrated").read_bytes(), original)
            self.assertFalse(legacy.exists())
            unblock_trust(root, "project/old", "old")
            self.assertEqual(load_trust(root)["skills"]["project/old"], previous)

    def test_backup_and_new_legacy_files_cannot_resurrect_revoked_approvals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = {"skills": {"project/old": {"state": "reviewed", "content_hash": "hash"}}}
            (root / "trust.json").write_text(json.dumps(stale))
            clear_trust(root, ["project/old"])
            (root / "trust.json").write_text(json.dumps(stale))
            self.assertEqual(trust_info(root, "project/old", "hash")["state"], "discovered")
            approvals.database_path(root).write_bytes(b"corrupted database")
            with self.assertRaisesRegex(ValueError, "approval database"):
                load_trust(root)
            approvals.database_path(root).write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "lost its schema"):
                set_trust(root, "project/new", "reviewed", "new", {})
            approvals.database_path(root).unlink()
            with self.assertRaisesRegex(ValueError, "database is missing"):
                load_trust(root)
            with self.assertRaisesRegex(ValueError, "database is missing"):
                set_trust(root, "project/new", "reviewed", "new", {})

    def test_version_and_approval_roll_back_together_and_history_is_append_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_trust(root, "project/one", "reviewed", "one", {})
            with self.assertRaisesRegex(ValueError, "does not match"):
                set_trust(root, "project/one", "reviewed", "two", {}, version={"source_key": "wrong", "content_hash": "two"})
            self.assertEqual(load_trust(root)["skills"]["project/one"]["content_hash"], "one")
            set_trust(root, "project/one", "blocked", "one", {}, reason="pause")
            unblock_trust(root, "project/one", "one")
            clear_trust(root, ["project/one"])
            with closing(sqlite3.connect(approvals.database_path(root))) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0], 4)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM content_versions").fetchone()[0], 0)
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    conn.execute("DELETE FROM decision_events")

    def test_indexed_read_does_not_load_whole_database_and_respects_project_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, catalog = root / "project", root / "catalog"
            set_trust(project, "id", "reviewed", "hash", {}, approval_key="global", approval_root=catalog, global_scope=True)
            set_trust(project, "id", "blocked", "hash", {})
            with patch.object(approvals, "load", side_effect=AssertionError("whole database read")):
                self.assertEqual(trust_info(project, "id", "hash", approval_key="global", approval_root=catalog)["state"], "blocked")
                self.assertEqual(trust_info(project, "id", "changed", approval_key="global", approval_root=catalog)["state"], "discovered")
            approvals.database_path(project).unlink()
            with self.assertRaisesRegex(ValueError, "database is missing"):
                load_trust(project)

    def test_database_and_sidecar_symlinks_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            target.write_text("untouched")
            db = approvals.database_path(root)
            db.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlinked"):
                load_trust(root)
            db.unlink()
            Path(str(db) + "-journal").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlinked"):
                set_trust(root, "id", "reviewed", "hash", {})
            self.assertEqual(target.read_text(), "untouched")


class SqliteConnectionTests(unittest.TestCase):
    def test_optional_sidecar_disappearance_during_validation_is_allowed(self) -> None:
        for suffix in ("-journal", "-wal", "-shm"):
            for moment in ("before-is-file", "ownership-stat"):
                with self.subTest(suffix=suffix, moment=moment), tempfile.TemporaryDirectory() as tmp:
                    db = Path(tmp) / "catalog.sqlite3"
                    with closing(database.connect_database(db, writable=True)) as connection:
                        connection.execute("CREATE TABLE preserved (value TEXT)")
                        connection.commit()
                    before = db.read_bytes()
                    sidecar = Path(str(db) + suffix)
                    sidecar.write_bytes(b"")
                    real_validate, real_is_file = database._assert_user_owned_regular_file, Path.is_file
                    validating = False
                    errors = []
                    def validate(candidate):
                        nonlocal validating
                        validating = candidate == sidecar
                        if validating and moment == "before-is-file":
                            sidecar.unlink()
                        try:
                            return real_validate(candidate)
                        except (FileNotFoundError, ValueError) as error:
                            errors.append(type(error))
                            raise
                        finally:
                            validating = False
                    def is_file(candidate, *args, **kwargs):
                        result = real_is_file(candidate, *args, **kwargs)
                        if result and validating and moment == "ownership-stat" and candidate == sidecar:
                            sidecar.unlink()
                        return result
                    if moment == "ownership-stat" and not hasattr(os, "geteuid"):
                        continue
                    with patch.object(database, "_assert_user_owned_regular_file", side_effect=validate), patch.object(Path, "is_file", is_file):
                        with closing(database.connect_database(db)) as connection:
                            self.assertEqual(connection.execute("SELECT COUNT(*) FROM preserved").fetchone()[0], 0)
                    self.assertEqual(errors, [ValueError if moment == "before-is-file" else FileNotFoundError])
                    self.assertFalse(sidecar.exists())
                    self.assertEqual(db.read_bytes(), before)

    def test_main_database_disappearance_is_still_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog.sqlite3"
            with closing(database.connect_database(db, writable=True)):
                pass
            real_validate = database._assert_user_owned_regular_file
            def disappear(candidate):
                if candidate == db:
                    db.unlink()
                return real_validate(candidate)
            with patch.object(database, "_assert_user_owned_regular_file", side_effect=disappear), self.assertRaisesRegex(ValueError, "non-file"):
                database.connect_database(db, writable=True)
            self.assertFalse(db.exists())

    def test_present_unsafe_optional_sidecars_are_still_refused(self) -> None:
        for suffix in ("-journal", "-wal", "-shm"):
            for kind in ("symlink", "dangling-symlink", "directory", "foreign-owner"):
                with self.subTest(suffix=suffix, kind=kind), tempfile.TemporaryDirectory() as tmp:
                    db = Path(tmp) / "catalog.sqlite3"
                    with closing(database.connect_database(db, writable=True)):
                        pass
                    sidecar = Path(str(db) + suffix)
                    if kind in {"symlink", "dangling-symlink"}:
                        sidecar.symlink_to(db if kind == "symlink" else db.parent / "missing")
                    elif kind == "directory":
                        sidecar.mkdir()
                    else:
                        if not hasattr(os, "geteuid"):
                            continue
                        sidecar.write_bytes(b"retained")
                    real_validate = database._assert_user_owned_regular_file
                    uid = os.geteuid() if hasattr(os, "geteuid") else 0
                    def validate(candidate):
                        if candidate == sidecar and kind == "foreign-owner":
                            with patch("skillager.state.statefiles.os.geteuid", return_value=uid + 1):
                                return real_validate(candidate)
                        return real_validate(candidate)
                    reason = "symlinked" if "symlink" in kind else "non-file" if kind == "directory" else "owned by another user"
                    with patch.object(database, "_assert_user_owned_regular_file", side_effect=validate), self.assertRaisesRegex(ValueError, reason):
                        database.connect_database(db)
                    self.assertTrue(sidecar.exists() or sidecar.is_symlink())


class SqliteSearchTests(unittest.TestCase):
    def test_owned_library_reuses_metadata_only_when_tree_and_provenance_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog, library = root / "catalog", root / "library"
            initialize_library(catalog, path=library, no_git=True)
            new_library_skill(catalog, "citrus")
            refresh_collection(catalog, "lib")
            with patch("skillager.catalog.impl._index_collection_skills", side_effect=AssertionError("rescanned unchanged library")):
                indexed = _load_or_refresh_collection_index(catalog, "lib")
                self.assertEqual(indexed["skills"][0]["id"], "lib/citrus")
            provenance_path = library / ".skillager" / "provenance.json"
            provenance = json.loads(provenance_path.read_text())
            provenance["skills"]["citrus"] = {"imported_from": {"skill_id": "original/citrus", "content_hash": "original", "source_type": "collection"}}
            provenance_path.write_text(json.dumps(provenance))
            changed = _load_or_refresh_collection_index(catalog, "lib")
            self.assertEqual(changed["skills"][0]["imported_from"]["skill_id"], "original/citrus")
            refresh_collection(catalog, "lib")
            skill = library / "skills" / "citrus" / "SKILL.md"
            skill.write_text(skill.read_text() + "\nOrchard maintenance.\n")
            edited = _load_or_refresh_collection_index(catalog, "lib")
            self.assertNotEqual(edited["skills"][0]["content_hash"], indexed["skills"][0]["content_hash"])

    def test_cache_reuse_scope_revocation_edit_and_rebuild_preserve_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "search.sqlite3"
            skills = []
            for index, body in enumerate(("oranges markerbefore", "oranges orchard")):
                source = root / str(index)
                source.mkdir()
                entry = source / "SKILL.md"
                entry.write_text(body)
                skills.append({"id": f"project/{index}", "name": f"Citrus {index}", "root": str(source), "entrypoint": str(entry), "content_hash": content_hash(source), "trust": "reviewed"})
            expected = search(skills, "oranges")
            self.assertEqual(search(skills, "oranges", cache_path=cache), expected)
            with patch("skillager.skills.search._verified_body_text", side_effect=AssertionError("body was reread")):
                self.assertEqual(search(skills, "oranges", cache_path=cache), expected)
            self.assertEqual([s["id"] for s in search(skills[:1], "oranges", cache_path=cache)], ["project/0"])
            skills[0]["trust"] = "discovered"
            self.assertEqual(search(skills, "markerbefore", include_untrusted=False, cache_path=cache), [])
            path = Path(skills[0]["entrypoint"])
            stat = path.stat()
            path.write_text("oranges markerafterx")
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            skills[0]["content_hash"] = content_hash(path.parent)
            skills[0]["trust"] = "reviewed"
            self.assertEqual(search(skills, "markerbefore", cache_path=cache), [])
            self.assertEqual([s["id"] for s in search(skills, "markerafterx", cache_path=cache)], ["project/0"])
            cache.unlink()
            self.assertEqual(search(skills, "oranges", cache_path=cache), search(skills, "oranges"))
            cache.write_bytes(b"corrupt cache")
            self.assertEqual(search(skills, "oranges", cache_path=cache), search(skills, "oranges"))
            self.assertNotIn("body", expected[0])

    def test_collection_legacy_read_and_sqlite_refresh_leave_authority_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "collections").mkdir()
            data = {"name": "example", "skills": [{"id": "example/one", "content_hash": "hash"}]}
            (root / "collections" / "example.json").write_text(json.dumps(data))
            self.assertEqual(_load_collection_index(root, "example"), data)
            self.assertFalse(cache_path(root).exists())
            write_collection(root, "example", data)
            self.assertEqual(_load_collection_index(root, "example"), data)
            cache_path(root).write_bytes(b"corrupt collection cache")
            write_collection(root, "example", data)
            self.assertEqual(_load_collection_index(root, "example"), data)
            self.assertFalse(approvals.database_path(root).exists())

    def test_missing_collection_cache_is_rebuilt_in_memory_on_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("# Citrus\n\nUse citrus techniques.\n")
            (root / "collections.json").write_text(json.dumps({"collections": {"example": {"path": str(source)}}}))
            result = _load_or_refresh_collection_index(root, "example")
            self.assertEqual(len(result["skills"]), 1)
            self.assertFalse(cache_path(root).exists())
