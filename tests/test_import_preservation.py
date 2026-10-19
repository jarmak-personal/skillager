from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import copy, deepcopy
from pathlib import Path
from unittest.mock import patch

from skillager.commands.impl import main
from skillager.exposure.plan import ExposurePlan
from skillager.exposure.plan_apply import apply_plan
from skillager.exposure.plan_sources import PlanSources
from skillager.exposure import plan_apply
from skillager.exposure.plan_targets import PlanTarget, tree_state
from skillager.library import importing
from skillager.library.service import accept_library_skill
from skillager.library.metadata import load_library_provenance, write_library_provenance
from skillager.library.model import LibraryLayout
from skillager.state import approvals
from tests.behavior import test_import_preservation as fixtures
from tests.behavior.support import BODY_SENTINEL
from tests.support import chdir


class ImportPreservationAuthorityTests(unittest.TestCase):
    def test_selected_observation_never_reads_unselected_bodies_in_5000_skill_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = fixtures.ImportPreservationBehaviorTests()
            project, cli, source, _ = fixture.fixture(root)
            library = root / "library/skills"
            for number in range(4999):
                path = library / f"other-{number:04d}"
                path.mkdir()
                (path / "SKILL.md").write_text(f"---\nname: other-{number}\ndescription: Use other guidance.\n---\n\n" + BODY_SENTINEL)
            fixture.observe(cli, source)
            original_open = Path.open
            reads = set()
            def confined(path, *args, **kwargs):
                if path.is_relative_to(library):
                    self.assertTrue(path.is_relative_to(library / "demo"), path)
                    reads.add(path)
                return original_open(path, *args, **kwargs)
            output = io.StringIO()
            with chdir(project), patch.dict(os.environ, cli.env), patch.object(Path, "open", confined), redirect_stdout(output):
                code = main(["expose", "--observe-native", str(source / "SKILL.md"), "--agent", "codex", "--json"])
            self.assertEqual(code, 0, output.getvalue())
            self.assertTrue(reads)
            self.assertNotIn(BODY_SENTINEL, output.getvalue())
            self.assertEqual(json.loads(output.getvalue())["status"], "available")

    def test_complete_candidate_limit_refuses_before_candidate_body_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = fixtures.ImportPreservationBehaviorTests()
            project, cli, source, _ = fixture.fixture(root)
            catalog = Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"])
            records = approvals.load(catalog)
            key, record = next(iter(records["global_approvals"].items()))
            for number in range(65):
                duplicate = deepcopy(record)
                duplicate["import_preservation"]["canonical"]["skill_id"] = f"lib/choice-{number}"
                records["global_approvals"][key.rsplit("#", 1)[0] + f"#choice-{number}"] = duplicate
            approvals.save(catalog, records)
            output = io.StringIO()
            with chdir(project), patch.dict(os.environ, cli.env), redirect_stdout(output), patch(
                "skillager.exposure.plan_sources.index_library_candidate", side_effect=AssertionError("overflow must precede candidate reads")
            ):
                code = main(["expose", "--observe-native", str(source / "SKILL.md"), "--agent", "codex", "--json"])
            self.assertEqual(code, 2)
            result = json.loads(output.getvalue())
            self.assertEqual(result["reason_code"], "request-limit")
            self.assertEqual(result["candidates"], [])

    def test_unbound_changed_and_pending_proofs_never_authorize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = fixtures.ImportPreservationBehaviorTests()
            _, cli, source, _ = fixture.fixture(root)
            catalog = Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"])
            original = approvals.load(catalog)
            for change in ("missing", "pending", "blocked", "malformed-state", "schema", "source-identity", "entrypoint", "project", "provider", "library", "root", "hash", "canonical-path", "decision-key", "derived"):
                with self.subTest(change=change):
                    records = deepcopy(original)
                    record = next(iter(records["global_approvals"].values()))
                    proof = record["import_preservation"]
                    if change == "missing":
                        record.pop("import_preservation")
                    elif change in {"pending", "blocked"}:
                        record["state"] = "discovered" if change == "pending" else "blocked"
                    elif change == "malformed-state":
                        record["state"] = ["reviewed"]
                    elif change == "schema":
                        proof["schema"] = "unbound"
                    elif change == "source-identity":
                        proof["source_identity"] = "f" * 64
                    elif change == "entrypoint":
                        proof["origin"]["entrypoint"] = str(source / "other.md")
                    elif change in {"project", "provider"}:
                        proof["origin"]["native"]["project_root" if change == "project" else "agent"] = "another"
                    elif change in {"library", "root", "hash", "canonical-path"}:
                        field = {"library": "library_id", "root": "library_root", "hash": "accepted_hash", "canonical-path": "path"}[change]
                        proof["canonical"][field] = "another"
                    elif change == "decision-key":
                        record["approval_key"] = "another"
                    else:
                        record["derived_from"] = {"lineage": {}}
                    if change == "malformed-state":
                        # Deliberately corrupt only this disposable authority's
                        # serialized record; ordinary writers reject this shape.
                        approvals.save(catalog, original)
                        with sqlite3.connect(approvals.database_path(catalog)) as connection:
                            connection.execute("UPDATE approvals SET record = ? WHERE scope = 'global_approvals'", (json.dumps(record),))
                    else:
                        approvals.save(catalog, records)
                    before = tree_state(source)
                    fixture.observe(cli, source, code=2)
                    self.assertEqual(tree_state(source), before)

    def test_pending_import_repair_does_not_backfill_and_response_loss_can_be_observed(self):
        for failure in ("trust", "response"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                fixture = fixtures.ImportPreservationBehaviorTests()
                project, cli, source, _ = fixture.fixture(root)
                catalog, state = Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"]), Path(cli.env["SKILLAGER_STATE_DIR"])
                with chdir(project), patch.dict(os.environ, cli.env):
                    preview = importing.import_preview(state, catalog, "project/demo", destination_name="second", project_dir=project)
                    owner = "set_trust" if failure == "trust" else "library_where"
                    with patch.object(importing, owner, side_effect=OSError("injected failure")):
                        with self.assertRaises((OSError, ValueError)):
                            importing.import_library_skill(state, catalog, "project/demo", destination_name="second",
                                expected_hash=preview["source_hash"], expected_source_key=preview["_source_key"], project_dir=project)
                    if failure == "trust":
                        self.assertNotIn("lib/second", {record.get("skill_id") for record in approvals.load(catalog)["global_approvals"].values()})
                        accept_library_skill(catalog, "second", expected_hash=preview["source_hash"], project_dir=project)
                observed = fixture.observe(cli, source)
                # Repair gives only ordinary canonical acceptance. A lost final
                # response after committed acceptance leaves observable facts.
                self.assertEqual(len(observed["candidates"]), 1 if failure == "trust" else 2)

    def test_locked_decision_drift_and_staged_failure_preserve_original(self):
        for failure in ("decision", "staging"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                fixture = fixtures.ImportPreservationBehaviorTests()
                project, cli, source, _ = fixture.fixture(root)
                request = fixture.observe(cli, source)["candidates"][0]["requests"]["stub"]
                state, catalog = Path(cli.env["SKILLAGER_STATE_DIR"]), Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"])
                scratch = root / "scratch"
                scratch.mkdir()
                before = tree_state(source)
                with chdir(project), patch.dict(os.environ, cli.env):
                    plan = ExposurePlan(request, state=state, catalog=catalog, project=project, agent="codex", scratch=scratch)
                    if failure == "decision":
                        def mutate(records):
                            next(iter(records["global_approvals"].values()))["reason"] = "changed decision"
                        approvals.mutate(catalog, mutate)
                        with self.assertRaisesRegex(ValueError, "approval changed"):
                            apply_plan(plan, plan.token)
                    else:
                        original_install = plan_apply._install
                        failed = False
                        def fail_once(candidate, target):
                            nonlocal failed
                            if not failed:
                                failed = True
                                raise OSError("injected staging failure")
                            return original_install(candidate, target)
                        with patch("skillager.exposure.plan_apply._install", side_effect=fail_once):
                            result, code = apply_plan(plan, plan.token)
                        self.assertEqual(code, 2)
                        self.assertEqual(result["status"], "partial")
                        self.assertEqual({item["target_id"] for item in result["results"]}, {item["target_id"] for item in result["targets"]})
                self.assertEqual(tree_state(source), before)

    def test_pinned_source_and_invalid_sync_relation_cannot_use_import_as_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = fixtures.ImportPreservationBehaviorTests()
            _, cli, source, _ = fixture.fixture(root)
            request = fixture.observe(cli, source)["candidates"][0]["requests"]["stub"]
            state = Path(cli.env["SKILLAGER_STATE_DIR"])
            approvals.save(state, {"skills": {"project/demo": {"state": "pinned", "content_hash": "f" * 64}}})
            pinned = fixture.observe(cli, source, code=2)
            self.assertEqual(pinned["candidates"][0]["reason_code"], "origin-pinned")
            approvals.save(state, {"skills": {}})
            catalog = Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"])
            proof = next(iter(approvals.load(catalog)["global_approvals"].values()))["import_preservation"]
            layout = LibraryLayout.from_root(root / "library")
            provenance = load_library_provenance(layout)
            provenance["skills"]["demo"]["sync"] = {"schema": "skillager.library-sync-lineage.v1", "source_identity": proof["source_identity"]}
            write_library_provenance(layout, provenance)
            before = tree_state(source)
            result = fixture.observe(cli, source, code=2)
            self.assertEqual(result["candidates"][0]["basis"], "approved-sync")
            self.assertIsNone(result["candidates"][0]["requests"])
            self.assertEqual(fixture.preview(cli, request, code=2)["reason_code"], "origin-unpreserved")
            provenance["skills"]["duplicate"] = deepcopy(provenance["skills"]["demo"])
            write_library_provenance(layout, provenance)
            self.assertEqual(fixture.observe(cli, source, code=2)["reason_code"], "origin-ambiguous")
            self.assertEqual(fixture.preview(cli, request, code=2)["reason_code"], "origin-ambiguous")
            self.assertEqual(tree_state(source), before)

    def test_canonical_changes_after_staging_preserve_original_before_disposal(self):
        for boundary in ("install", "disposal"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                fixture = fixtures.ImportPreservationBehaviorTests()
                project, cli, source, _ = fixture.fixture(root)
                request = fixture.observe(cli, source)["candidates"][0]["requests"]["stub"]
                state, catalog = Path(cli.env["SKILLAGER_STATE_DIR"]), Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"])
                scratch = root / "scratch"
                scratch.mkdir()
                before = tree_state(source)
                original_install, original_observe = plan_apply._install, PlanTarget.observe
                changed, backup_observations = False, 0
                def change():
                    (root / "library/skills/demo/support/helper.sh").write_text("Concurrent canonical change\n")
                def install(candidate, target):
                    nonlocal changed
                    result = original_install(candidate, target)
                    if boundary == "install" and not changed:
                        changed = True
                        change()
                    return result
                def observe(target, path=None):
                    nonlocal backup_observations
                    result = original_observe(target, path)
                    if path is not None and path.name == "previous":
                        backup_observations += 1
                        if boundary == "disposal" and backup_observations == 3:
                            change()
                    return result
                with chdir(project), patch.dict(os.environ, cli.env):
                    plan = ExposurePlan(request, state=state, catalog=catalog, project=project, agent="codex", scratch=scratch)
                    with patch("skillager.exposure.plan_apply._install", side_effect=install), patch.object(PlanTarget, "observe", observe):
                        result, code = apply_plan(plan, plan.token)
                self.assertEqual(code, 2, result)
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["reason_code"], "source-changed")
                if boundary == "install":
                    self.assertEqual(tree_state(source), before)
                else:
                    outcome = next(item for item in result["results"] if item["path"] == str(source))
                    self.assertEqual(outcome["status"], "recovery_required")
                    self.assertEqual(tree_state(Path(outcome["recovery_path"])), before)
                self.assertEqual((root / "library/skills/demo/support/helper.sh").read_text(), "Concurrent canonical change\n")

    def test_foreign_import_proof_does_not_hide_current_library_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = fixtures.ImportPreservationBehaviorTests()
            _, cli, source, _ = fixture.fixture(root)
            catalog = Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"])
            current = approvals.load(catalog)
            foreign = copy(cli)
            foreign_catalog = root / "foreign-catalog"
            foreign.env = {**cli.env, "SKILLAGER_CATALOG_STATE_DIR": str(foreign_catalog)}
            fixture.checked(foreign.run("library", "init", "--path", str(root / "foreign-library"), "--no-git", "--json"))
            preview = fixture.checked(foreign.run("import", "project/demo", "--source-file", str(source / "SKILL.md"), "--review-manifest", "--json"))
            fixture.checked(foreign.run(*preview["next_command_argv"][1:]))
            foreign_records = approvals.load(foreign_catalog)["global_approvals"]
            combined = deepcopy(current)
            combined["global_approvals"].update(foreign_records)
            approvals.save(catalog, combined)
            observed = fixture.observe(cli, source)
            self.assertEqual(observed["status"], "available")
            self.assertEqual(len(observed["candidates"]), 1)
            request = observed["candidates"][0]["requests"]["stub"]
            self.assertEqual(request["source"]["library_id"], observed["library_id"])
            # A coherent foreign decision alone is never current-library authority.
            approvals.save(catalog, {**current, "global_approvals": foreign_records})
            self.assertEqual(fixture.observe(cli, source, code=2)["candidates"], [])
            # A proof claiming this library under a foreign key is malformed, not
            # an unrelated foreign choice that may be silently ignored.
            misbound = deepcopy(combined)
            key = next(iter(foreign_records))
            misbound["global_approvals"][key]["import_preservation"]["canonical"]["library_id"] = observed["library_id"]
            approvals.save(catalog, misbound)
            self.assertEqual(fixture.observe(cli, source, code=2)["reason_code"], "origin-unpreserved")
            approvals.save(catalog, combined)
            foreign_before = tree_state(root / "foreign-library/skills/demo")
            fixture.apply(cli, fixture.preview(cli, request))
            self.assertEqual(tree_state(root / "foreign-library/skills/demo"), foreign_before)

    def test_malformed_decisions_after_install_report_partial_and_preserve_original(self):
        for boundary in ("install", "disposal"):
            for malformed in ("record", "state", "proof"):
                with self.subTest(boundary=boundary, malformed=malformed), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp).resolve()
                    fixture = fixtures.ImportPreservationBehaviorTests()
                    project, cli, source, _ = fixture.fixture(root)
                    request = fixture.observe(cli, source)["candidates"][0]["requests"]["stub"]
                    preview = fixture.preview(cli, request)
                    before = tree_state(source)
                    canonical_check = PlanSources.revalidate_canonical
                    checks_after_install = 0
                    def corrupt(owner, records, **kwargs):
                        nonlocal checks_after_install
                        if (source / "skillager.materialized.yaml").is_file():
                            checks_after_install += 1
                            if checks_after_install == (1 if boundary == "install" else 2):
                                decisions = records[owner.catalog]["global_approvals"]
                                key = next(iter(decisions))
                                if malformed == "record":
                                    decisions[key] = ["malformed"]
                                else:
                                    decisions[key]["state" if malformed == "state" else "import_preservation"] = ["malformed"]
                        return canonical_check(owner, records, **kwargs)
                    output = io.StringIO()
                    with chdir(project), patch.dict(os.environ, cli.env), redirect_stdout(output), patch.object(PlanSources, "revalidate_canonical", corrupt):
                        code = main(preview["next_command_argv"][1:])
                    result = json.loads(output.getvalue())
                    self.assertEqual(code, 2, result)
                    self.assertEqual(result["status"], "partial")
                    self.assertEqual(result["reason_code"], "approval-malformed")
                    self.assertEqual({item["target_id"] for item in result["results"]}, {item["target_id"] for item in preview["targets"]})
                    outcome = next(item for item in result["results"] if item["path"] == str(source))
                    if boundary == "install":
                        self.assertEqual(outcome["status"], "rolled_back")
                        self.assertEqual(tree_state(source), before)
                    else:
                        self.assertEqual(outcome["status"], "recovery_required")
                        self.assertEqual(tree_state(Path(outcome["recovery_path"])), before)

    def test_malformed_provenance_after_staging_uses_existing_partial_recovery(self):
        for malformed in ("entry", "sync"):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                fixture = fixtures.ImportPreservationBehaviorTests()
                project, cli, source, _ = fixture.fixture(root)
                request = fixture.observe(cli, source)["candidates"][0]["requests"]["stub"]
                preview = fixture.preview(cli, request)
                before = tree_state(source)
                revalidate = PlanSources.revalidate
                injected = False
                def corrupt(owner, records=None):
                    nonlocal injected
                    if not injected and any(source.parent.glob(".skillager-exposure-plan-*")):
                        injected = True
                        provenance = load_library_provenance(owner.layout)
                        provenance["skills"]["demo"] = ["malformed"] if malformed == "entry" else {"sync": ["malformed"]}
                        write_library_provenance(owner.layout, provenance)
                    return revalidate(owner, records)
                output = io.StringIO()
                with chdir(project), patch.dict(os.environ, cli.env), redirect_stdout(output), patch.object(PlanSources, "revalidate", corrupt):
                    code = main(preview["next_command_argv"][1:])
                result = json.loads(output.getvalue())
                self.assertTrue(injected)
                self.assertEqual(code, 2, result)
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["reason_code"], "origin-unpreserved")
                self.assertEqual(tree_state(source), before)
                self.assertFalse(list(source.parent.glob(".skillager-exposure-plan-*")))
