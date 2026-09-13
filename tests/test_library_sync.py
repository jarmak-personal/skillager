from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skillager.library import sync_mutation
from skillager.library.importing import import_inventory
from skillager.library.metadata import load_library_identity, write_library_identity
from skillager.library.model import LibraryIdentity, LibraryLayout
from skillager.library.service import initialize_library
from skillager.library.sync import sync_approved
from skillager.state import approvals
from skillager.state.trust import set_trust
from tests.support import chdir


class LibrarySyncMutationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "pyproject.toml").write_text('[project]\nname = "demo"\n')
        self.state, self.catalog, self.library = (self.root / name for name in ("state", "catalog", "library"))
        self.enterContext(patch("pathlib.Path.home", return_value=self.root / "home"))
        self.enterContext(chdir(self.project))
        initialize_library(self.catalog, path=self.library, no_git=True)
        self.source = self.add_source("initial")

    def add_source(self, name):
        target = self.project / ".skills" / name
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Use precise project guidance.\n---\n\nUse precise project guidance.\n")
        return self.approve_source(target)

    def approve_source(self, target):
        skill = next(skill for skill in import_inventory(self.state, self.catalog)["skills"] if Path(skill["root"]) == target)
        set_trust(self.state, skill["id"], "reviewed", skill["content_hash"], skill["source"], lint=skill.get("lint"))
        return skill

    def sync(self, **kwargs):
        return sync_approved(self.state, self.catalog, project_dir=self.project, **kwargs)

    def derived(self):
        result = self.sync()
        self.assertEqual(result["counts"]["created"], 1, result)
        item = next(item for item in result["items"] if item["outcome"] == "created")
        target = self.library / "skills" / item["canonical_skill_id"].removeprefix("lib/")
        return target

    def revise(self):
        target = Path(self.source["root"])
        with (target / "SKILL.md").open("a") as handle:
            handle.write("\nA reviewed revision.\n")
        self.source = self.approve_source(target)

    def test_pin_or_block_entered_during_staging_protects_destination_before_write(self):
        for target_decision in ("pinned", "blocked", "source-blocked"):
            with self.subTest(target_decision=target_decision):
                target = self.derived()
                self.revise()
                before = (target / "SKILL.md").read_bytes()
                provenance = (self.library / ".skillager" / "provenance.json").read_bytes()
                prepare = sync_mutation.prepare_library_candidate
                def protect(*args, **kwargs):
                    value = prepare(*args, **kwargs)
                    if target_decision == "source-blocked":
                        set_trust(self.state, self.source["id"], "blocked", self.source["content_hash"], self.source["source"])
                    else:
                        key = f"library:{load_library_identity(LibraryLayout.from_root(self.library)).library_id}#{target.name}"
                        record = approvals.get_record(self.catalog, "global_approvals", key)
                        set_trust(self.catalog, f"lib/{target.name}", target_decision, record["content_hash"], record["source"],
                                  approval_key=key, approval_root=self.catalog, global_scope=True)
                    return value
                with patch.object(sync_mutation, "prepare_library_candidate", side_effect=protect):
                    result = self.sync()
                self.assertEqual(result["counts"]["failed"], 1, result)
                self.assertEqual(next(item for item in result["items"] if item["outcome"] == "failed")["phase"], "prepared")
                self.assertEqual((target / "SKILL.md").read_bytes(), before)
                self.assertEqual((self.library / ".skillager" / "provenance.json").read_bytes(), provenance)
                self.assertFalse(list(self.root.glob(".skillager-sync-*")))
                # Each case owns a new source/destination, retaining protected copies.
                self.source = self.add_source(target_decision)

    def test_source_byte_race_and_library_uuid_race_refuse_before_publication(self):
        for race in ("source", "identity"):
            with self.subTest(race=race):
                prepare = sync_mutation.prepare_library_candidate
                original_identity = load_library_identity(LibraryLayout.from_root(self.library))
                def change(*args, **kwargs):
                    value = prepare(*args, **kwargs)
                    if race == "source":
                        (Path(self.source["root"]) / "SKILL.md").write_text("Changed after review.")
                    else:
                        write_library_identity(LibraryLayout.from_root(self.library), LibraryIdentity(
                            library_id="12345678-1234-1234-1234-123456789012", git_mode="disabled", created_at=original_identity.created_at))
                    return value
                with patch.object(sync_mutation, "prepare_library_candidate", side_effect=change):
                    result = self.sync()
                self.assertEqual(result["counts"]["failed"], 1, result)
                self.assertFalse(list((self.library / "skills").glob("*/SKILL.md")))
                write_library_identity(LibraryLayout.from_root(self.library), original_identity)
                self.source = self.add_source(race)

    def test_destination_change_during_staging_is_preserved(self):
        target = self.derived()
        self.revise()
        before = (target / "SKILL.md").read_bytes()
        prepare = sync_mutation.prepare_library_candidate
        def customize(*args, **kwargs):
            value = prepare(*args, **kwargs)
            (target / "new.tmp").write_text("An uncommitted user note.")
            return value
        with patch.object(sync_mutation, "prepare_library_candidate", side_effect=customize):
            result = self.sync()
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual((target / "SKILL.md").read_bytes(), before)
        self.assertEqual((target / "new.tmp").read_text(), "An uncommitted user note.")

    def test_late_detached_previous_customization_is_retained_with_recovery_path(self):
        self.derived()
        self.revise()
        verify = sync_mutation.verified_version_references
        def customize(*args, **kwargs):
            result = verify(*args, **kwargs)
            backup = next(self.root.glob(".skillager-sync-*/*.previous"))
            (backup / "keep.tmp").write_text("Do not discard this late change.")
            return result
        with patch.object(sync_mutation, "verified_version_references", side_effect=customize):
            result = self.sync()
        self.assertEqual(result["counts"]["conflict"], 1, result)
        item = result["items"][0]
        self.assertEqual(item["phase"], "accepted")
        self.assertEqual((Path(item["recovery_path"]) / "keep.tmp").read_text(), "Do not discard this late change.")

    def test_derivation_failure_reports_published_pending_copy_without_approval(self):
        original = approvals._write_record
        def fail_derived(*args, **kwargs):
            if kwargs.get("action") == "derive-library":
                raise OSError("fixture approval storage failure")
            return original(*args, **kwargs)
        with patch.object(approvals, "_write_record", side_effect=fail_derived):
            result = self.sync()
        self.assertEqual(result["status"], "partial", result)
        self.assertEqual(result["counts"]["created"], 0)
        self.assertEqual(result["items"][0]["phase"], "published")
        self.assertEqual(result["items"][0]["repair"], "accept-pending")
        observed = self.sync(status_only=True)
        self.assertEqual(observed["lineages"][0]["preservation"], "pending")
        self.assertIsNone(observed["lineages"][0]["canonical"]["accepted_hash"])
        repeat = self.sync()
        self.assertEqual(repeat["counts"]["conflict"], 1)

    def test_later_chunk_failure_does_not_erase_first_accepted_outcome(self):
        self.add_source("second")
        prepare = sync_mutation.prepare_library_candidate
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("second source unavailable")
            return prepare(*args, **kwargs)
        with patch.object(sync_mutation, "CHUNK_SKILLS", 1), patch.object(sync_mutation, "prepare_library_candidate", side_effect=fail_second):
            result = self.sync()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["counts"]["created"], 1)
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(len(self.sync(status_only=True)["lineages"]), 1)

    def test_initialization_exception_is_uncertain_after_possible_effects(self):
        from skillager.catalog.impl import load_collections, save_collections
        config = load_collections(self.catalog)
        config["collections"].pop("lib")
        save_collections(self.catalog, config)
        with patch("skillager.library.sync.initialize_library", side_effect=OSError("fixture late init failure")):
            result = self.sync()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["items"][0]["phase"], "unknown")

    def test_bound_selection_refuses_removed_registration_and_root_alias(self):
        from skillager.catalog.impl import load_collections, save_collections
        identity = load_library_identity(LibraryLayout.from_root(self.library))
        config = load_collections(self.catalog)
        original = config["collections"].pop("lib")
        save_collections(self.catalog, config)
        with self.assertRaisesRegex(ValueError, "selection changed"):
            self.sync(expected_library_id=identity.library_id, expected_library_root=self.library)
        self.assertFalse(list((self.library / "skills").glob("*/SKILL.md")))
        config["collections"]["lib"] = original
        save_collections(self.catalog, config)
        moved = self.root / "moved-library"
        self.library.rename(moved)
        self.library.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "selection changed"):
            self.sync(expected_library_id=identity.library_id, expected_library_root=self.library)
        self.assertFalse(list((moved / "skills").glob("*/SKILL.md")))

    def test_status_coverage_counts_observed_eligible_sources_without_mutating(self):
        observed = self.sync(status_only=True)
        self.assertEqual(observed["coverage"]["processed_sources"], 1)
        self.assertEqual(observed["candidates"][0]["state"], "eligible-create")
        self.assertEqual(observed["lineages"], [])
        self.assertFalse(list((self.library / "skills").glob("*/SKILL.md")))

    def test_deadline_includes_inventory_and_stops_before_initial_candidate(self):
        with patch("skillager.library.sync.SOFT_DEADLINE_SECONDS", 0.0):
            result = self.sync()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["items"][0]["reason_code"], "time-limit")
        self.assertEqual(result["coverage"]["processed_sources"], 0)
        self.assertFalse(list((self.library / "skills").glob("*/SKILL.md")))

    def test_combined_projected_capacity_refuses_creation_but_keeps_source_approval(self):
        from skillager.catalog.impl import load_collections, save_collections
        self.add_source("second")
        config = load_collections(self.catalog)
        config["collections"].pop("lib")
        save_collections(self.catalog, config)
        with patch("skillager.library.sync.SYNC_INVENTORY_LIMIT", 3):
            result = self.sync()
            observed = self.sync(status_only=True)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["counts"]["created"], 0)
        self.assertTrue(all(item["reason_code"] == "inventory-limit" for item in result["items"]))
        self.assertEqual(observed["coverage"]["processed_sources"], 2)
        self.assertTrue(all(item["state"] == "unavailable" for item in observed["candidates"]))
        self.assertFalse((self.root / "home" / ".skillager" / "library").exists())
        self.assertEqual(approvals.get_record(self.state, "skills", self.source["id"])["state"], "reviewed")

    def test_selected_approval_admission_counts_unselected_effective_sources(self):
        self.add_source("second")
        self.add_source("third")
        with patch("skillager.library.sync.SYNC_INVENTORY_LIMIT", 3):
            result = self.sync(skills=[self.source])
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["coverage"]["discovered_origins"], 3)
        self.assertEqual(result["coverage"]["selected_sources"], 1)
        self.assertFalse(list((self.library / "skills").glob("*/SKILL.md")))

    def test_complete_inventory_is_reused_and_mapping_refresh_are_per_batch(self):
        from skillager.library import sync
        self.add_source("second")
        self.add_source("third")
        inventory = import_inventory(self.state, self.catalog)
        with patch.object(sync, "import_inventory", side_effect=AssertionError("must reuse full inventory")), \
             patch.object(sync, "_load_state", wraps=sync._load_state) as mapping, \
             patch.object(sync, "refresh_collection", wraps=sync.refresh_collection) as refresh, \
             patch.object(sync_mutation, "CHUNK_SKILLS", 2), \
             patch.object(sync_mutation, "verified_version_references", wraps=sync_mutation.verified_version_references) as versions:
            result = self.sync(inventory=inventory)
        self.assertEqual(result["counts"]["created"], 3, result)
        self.assertEqual(mapping.call_count, 1)
        self.assertEqual(refresh.call_count, 1)
        self.assertEqual(versions.call_count, 2)

    def test_bound_existing_capacity_refusal_is_not_library_changed(self):
        identity = load_library_identity(LibraryLayout.from_root(self.library))
        self.derived()
        with patch("skillager.library.sync.SYNC_INVENTORY_LIMIT", 1):
            for status_only in (False, True):
                result = self.sync(status_only=status_only, expected_library_id=identity.library_id, expected_library_root=self.library)
                self.assertEqual(result["reason_code"], "inventory-limit")
                self.assertEqual(result["library"]["library_id"], identity.library_id)
        self.assertEqual(approvals.get_record(self.state, "skills", self.source["id"])["state"], "reviewed")

    def test_response_limit_refuses_before_copy_or_initialization(self):
        with patch("skillager.library.sync.SYNC_RESULT_BYTES", 1):
            result = self.sync()
        self.assertEqual(result["reason_code"], "result-limit")
        self.assertFalse(list((self.library / "skills").glob("*/SKILL.md")))

    def test_staging_chunk_reserves_capacity_before_copying_next_candidate(self):
        from skillager.skills.tree import ContentTreeLimits
        self.add_source("second")
        self.add_source("third")
        with patch.object(sync_mutation, "CHUNK_BYTES", 220), \
             patch.object(sync_mutation, "COPY_LIMITS", ContentTreeLimits(512, 150, 150)), \
             patch.object(sync_mutation, "verified_version_references", wraps=sync_mutation.verified_version_references) as versions:
            result = self.sync()
        self.assertEqual(result["counts"]["created"], 3, result)
        self.assertEqual(versions.call_count, 3)

    def test_bounded_copy_refuses_growth_before_writing_excess_bytes(self):
        from skillager.skills.tree import ContentTreeLimits, copy_content_tree, iter_content_files
        source = Path(self.source["root"])
        destination = self.root / "bounded-copy"
        original = Path.open
        def grow(path, *args, **kwargs):
            if path == source / "SKILL.md" and args == ("rb",):
                with original(path, "ab") as writer:
                    writer.write(b"X" * 1000)
            return original(path, *args, **kwargs)
        size = sum(path.stat().st_size for path in iter_content_files(source))
        with patch.object(Path, "open", grow), self.assertRaisesRegex(ValueError, "grew beyond"):
            copy_content_tree(source, destination, limits=ContentTreeLimits(512, size, size))
        self.assertLessEqual(sum(path.stat().st_size for path in iter_content_files(destination)), size)

    def test_verified_copy_preserves_binary_support_and_executable_mode(self):
        source = Path(self.source["root"])
        (source / "support.bin").write_bytes(bytes(range(256)))
        (source / "runner.sh").write_text("#!/bin/sh\nprintf help\n")
        (source / "runner.sh").chmod(0o755)
        self.source = self.approve_source(source)
        target = self.derived()
        self.assertEqual((target / "support.bin").read_bytes(), bytes(range(256)))
        self.assertEqual((target / "runner.sh").stat().st_mode & 0o111, 0o111)
        self.assertEqual(self.sync(status_only=True)["lineages"][0]["preservation"], "verified")

    def test_selected_source_disappearance_is_not_a_completed_empty_batch(self):
        import shutil
        shutil.rmtree(Path(self.source["root"]))
        result = self.sync(skills=[self.source])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(result["items"][0]["reason_code"], "source-missing")
        self.assertFalse(list((self.library / "skills").glob("*/SKILL.md")))

    def test_retained_historical_origins_share_capacity_with_authored_entries(self):
        from skillager.library.sync_lineage import projected_metadata_limits
        lineages = [{"source_identity": "source", "origins": [{"origin_id": "old-place"}]}]
        self.assertEqual(projected_metadata_limits(lineages, [], [], self.library, {"current-place"}, 2, 3, 32 * 1024 * 1024), "inventory-limit")

    def test_failed_candidate_is_removed_before_the_next_staging_attempt(self):
        self.add_source("second")
        prepare = sync_mutation.prepare_library_candidate
        attempts = []
        def fail_first(*args, **kwargs):
            if attempts:
                self.assertFalse(attempts[0].exists(), "failed unpublished staging must be removed before the next candidate")
            attempts.append(Path(args[1]))
            result = prepare(*args, **kwargs)
            if len(attempts) == 1:
                raise ValueError("source changed after bounded copy")
            return result
        with patch.object(sync_mutation, "prepare_library_candidate", side_effect=fail_first):
            result = self.sync()
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(result["counts"]["created"], 1)
        self.assertFalse(list(self.root.glob(".skillager-sync-*")))

    def test_incomplete_failed_candidate_cleanup_retains_and_stops_staging(self):
        self.add_source("second")
        prepare = sync_mutation.prepare_library_candidate
        attempted = []
        remove = sync_mutation.shutil.rmtree
        def fail(*args, **kwargs):
            attempted.append(Path(args[1]))
            prepare(*args, **kwargs)
            raise ValueError("candidate refused after bounded copy")
        def retain(path, *args, **kwargs):
            if Path(path) in attempted:
                raise OSError("fixture private cleanup unavailable")
            return remove(path, *args, **kwargs)
        with patch.object(sync_mutation, "prepare_library_candidate", side_effect=fail), patch.object(sync_mutation.shutil, "rmtree", side_effect=retain):
            result = self.sync()
        self.assertEqual(len(attempted), 1)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["counts"]["uncertain"], 1)
        item = next(item for item in result["items"] if item["outcome"] == "uncertain")
        self.assertTrue(Path(item["recovery_path"]).is_dir())
        self.assertEqual(result["coverage"]["processed_sources"], 1)
        self.assertFalse(list((self.library / "skills").glob("*/SKILL.md")))

    def test_collection_migration_alias_derives_actual_old_decision_and_respects_precedence(self):
        from copy import deepcopy
        from skillager.catalog.impl import add_collection, refresh_collection
        from skillager.catalog.storage import write_collection
        from skillager.state.library_approval import approval_witness
        from skillager.state.trust import content_hash
        collection = self.root / "collection"
        source = collection / "python" / "guide"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("# Guide\n\nUse precise Python project guidance.\n")
        digest = content_hash(source)
        add_collection(self.catalog, "personal", collection)
        write_collection(self.catalog, "personal", {"schema": "skillager.collection-index.v1", "name": "personal",
            "path": str(collection), "skills": [{"id": "personal/guide", "root": str(source), "content_hash": digest}], "errors": []})
        original = set_trust(self.state, "personal/guide", "reviewed", digest, {"type": "collection", "collection": "personal"})
        # Refresh in the catalog authority, where the old project-only decision is
        # absent. Project observation therefore uses the retained migration alias.
        refresh_collection(self.catalog, "personal")
        observed = next(skill for skill in import_inventory(self.state, self.catalog)["skills"] if skill["id"] == "personal/python/guide")
        self.assertEqual(observed["_decision_skill_id"], "personal/guide")
        self.assertIsNone(approvals.get_record(self.state, "skills", observed["id"]))
        records = {root: approvals.load(root) for root in (self.state, self.catalog)}
        competing = deepcopy(records)
        competing[self.catalog].setdefault("global_approvals", {})[observed["approval_key"]] = {**original, "scope": "global", "skill_id": observed["id"]}
        self.assertEqual(approval_witness(observed, self.state, self.catalog, competing)["scope"], "global")
        competing[self.state]["skills"][observed["id"]] = {**original, "state": "blocked"}
        self.assertIsNone(approval_witness(observed, self.state, self.catalog, competing))
        result = self.sync(skills=[observed])
        self.assertEqual(result["counts"]["created"], 1, result)
        relation = next(value for value in self.sync(status_only=True)["lineages"] if value["canonical"]["skill_id"] == result["items"][0]["canonical_skill_id"])
        self.assertEqual(relation["source_approval"]["decision_skill_id"], "personal/guide")
        self.assertEqual(relation["source_approval"]["scope"], "project")
        self.assertEqual(approvals.get_record(self.state, "skills", "personal/guide"), original)
        self.assertIsNone(approvals.get_record(self.state, "skills", observed["id"]))

    def test_setup_explicit_clone_sees_conflicting_effective_clone_before_sync(self):
        from skillager.skills.review import setup_environment
        for include_blocked in (True, False):
            with self.subTest(include_blocked=include_blocked):
                default = self.project / f"default-clone-{include_blocked}"
                explicit = self.root / f"explicit-clone-{include_blocked}"
                for clone, body in ((default, "Use current project Python guidance."), (explicit, "Use distinct reviewed Python guidance.")):
                    (clone / ".git").mkdir(parents=True)
                    (clone / ".git" / "config").write_text('[remote "origin"]\nurl = https://example.invalid/shared-' + str(include_blocked) + '.git\n')
                    (clone / "pyproject.toml").write_text('[project]\nname = "clone"\n')
                    skill = clone / ".agents" / "skills" / "guide"
                    skill.mkdir(parents=True)
                    (skill / "SKILL.md").write_text("# Guide\n\n" + body + "\n")
                current = next(skill for skill in import_inventory(self.state, self.catalog)["skills"] if Path(skill["root"]) == default / ".agents" / "skills" / "guide")
                set_trust(self.state, current["id"], "reviewed", current["content_hash"], current["source"])
                from skillager.library import sync
                with patch.object(sync, "import_inventory", wraps=sync.import_inventory) as discovery:
                    report = setup_environment(self.state, paths=[explicit / ".agents" / "skills"], include_packages=True,
                        include_blocked=include_blocked, accept_low=True, approval_root=self.catalog)
                result = report["action"]["library_sync"]
                self.assertEqual(discovery.call_count, 1)
                self.assertEqual(result["counts"]["conflict"], 1, result)
                self.assertEqual(result["items"][0]["reason_code"], "source-version-conflict")
                self.assertEqual(len(result["items"][0]["origin_ids"]), 2)
                self.assertGreaterEqual(result["coverage"]["discovered_origins"], 3)
                self.assertFalse((self.state / "status_scope.json").exists())
                self.assertFalse(list((self.library / "skills").glob("*/SKILL.md")))

    def test_setup_unsaved_explicit_root_syncs_only_its_approved_selection(self):
        from skillager.skills.review import setup_environment
        source = self.root / "explicit" / "guide"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("# Guide\n\nUse explicitly selected project guidance.\n")
        report = setup_environment(self.state, paths=[source], accept_low=True, approval_root=self.catalog)
        result = report["action"]["library_sync"]
        self.assertEqual(result["counts"]["created"], 1, result)
        self.assertEqual(result["coverage"]["discovered_origins"], 2)
        self.assertEqual(result["coverage"]["selected_sources"], 1)
        self.assertFalse((self.state / "status_scope.json").exists())

    def test_unavailable_approval_state_retains_only_actual_discovery_error_count(self):
        inventory = import_inventory(self.state, self.catalog)
        inventory["errors"] = [{"path": "/fixture-missing", "error": "unreadable"}]
        with patch("skillager.library.sync._load_state", side_effect=ValueError("approval storage unavailable")):
            result = self.sync(inventory=inventory)
        self.assertEqual(result["reason_code"], "sync-unavailable")
        self.assertEqual(result["coverage"]["discovery_error_count"], 1)

    def test_editable_provenance_cannot_retarget_catalog_bound_lineage(self):
        from copy import deepcopy
        from skillager.library.metadata import load_library_provenance, write_library_provenance
        target = self.derived()
        layout = LibraryLayout.from_root(self.library)
        original = load_library_provenance(layout)
        for field in ("lineage_id", "source_identity", "source_approval", "origins", "target_state"):
            with self.subTest(field=field):
                changed = deepcopy(original)
                lineage = changed["skills"][target.name]["sync"]
                if field == "source_approval":
                    lineage[field]["evidence_id"] = "0" * 64
                    lineage[field]["record"] = {"reason": "PRIVATE_RECORD_SENTINEL"}
                elif field == "origins":
                    lineage[field][0]["path"] = "/unpreserved-origin"
                elif field == "target_state":
                    lineage[field]["mode"] = 0
                else:
                    lineage[field] = "0" * 64
                write_library_provenance(layout, changed)
                observed = self.sync(status_only=True)
                self.assertTrue(all(value["preservation"] != "verified" for value in observed["lineages"]))
                self.assertNotIn("PRIVATE_RECORD_SENTINEL", str(observed))
                result = self.sync()
                self.assertEqual(result["counts"]["created"] + result["counts"]["updated"], 0)
                write_library_provenance(layout, original)

    def test_review_reused_inventory_preserves_collection_discovery_errors(self):
        from contextlib import redirect_stdout
        from io import StringIO
        import json
        import os
        from skillager.catalog.impl import add_collection
        from skillager.cli import main
        from skillager.library import sync
        from skillager.catalog import impl as catalog_owner
        collection = self.root / "unreadable-collection"
        collection.mkdir()
        add_collection(self.catalog, "broken", collection)
        original = catalog_owner._load_or_refresh_collection_index
        def load(state, name, **kwargs):
            if name == "broken":
                return {"skills": [], "errors": [{"path": str(collection), "error": "fixture read refused"}]}
            return original(state, name, **kwargs)
        output = StringIO()
        with patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(self.state), "SKILLAGER_CATALOG_STATE_DIR": str(self.catalog)}), \
             patch.object(catalog_owner, "_load_or_refresh_collection_index", side_effect=load), \
             patch.object(sync, "import_inventory", side_effect=AssertionError("complete review inventory must be reused")), redirect_stdout(output):
            self.assertEqual(main(["review", "approve", self.source["id"], "--include-blocked", "--json"]), 0)
        result = json.loads(output.getvalue())["action"]["library_sync"]
        self.assertEqual(result["counts"]["created"], 1, result)
        self.assertEqual(result["coverage"]["discovery_error_count"], 1)
        self.assertFalse(result["coverage"]["complete"])
