"""Compound stages retain real authority, locks and truthful post-dispatch results."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from skillager.commands.impl import main
from skillager.exposure.plan_targets import tree_state
from skillager.library import import_placement
from skillager.state.locking import ResourceLockTimeout, resource_lock
from tests.behavior import test_import_placement as fixtures
from tests.support import chdir


class ImportPlacementAuthorityTests(unittest.TestCase):
    def fixture(self, root, initialized=True):
        fixture = fixtures.ImportPlacementBehaviorTests()
        project, cli, source, library, skill_id = fixture.fixture(root, initialized=initialized)
        preview = fixture.preview(cli, source, skill_id)
        return fixture, project, cli, source, library, preview

    def invoke(self, project, cli, command):
        output = io.StringIO()
        with chdir(project), patch.dict(os.environ, cli.env), redirect_stdout(output):
            code = main(command[1:])
        return code, json.loads(output.getvalue())

    def test_same_library_locks_span_first_initialization_import_and_adoption(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, project, cli, _, _, preview = self.fixture(Path(tmp).resolve(), initialized=False)
            catalog = Path(cli.env["SKILLAGER_CATALOG_STATE_DIR"])
            seen = []

            def guarded(name, original):
                def call(*args, **kwargs):
                    for resource in (catalog / "library-mutation", catalog / "library-skill-demo"):
                        with self.assertRaises(ResourceLockTimeout):
                            with resource_lock(resource, timeout=0):
                                pass
                    seen.append(name)
                    return original(*args, **kwargs)
                return call

            with patch.object(import_placement, "initialize_library_locked",
                              guarded("initialize", import_placement.initialize_library_locked)), patch.object(
                    import_placement, "import_library_skill_locked",
                    guarded("import", import_placement.import_library_skill_locked)), patch.object(
                    import_placement, "apply_plan_locked", guarded("adopt", import_placement.apply_plan_locked)):
                code, result = self.invoke(project, cli, preview["next_command_argv"])
            self.assertEqual(code, 0, result)
            self.assertEqual(seen, ["initialize", "import", "adopt"])
            self.assertEqual(result["status"], "saved-and-replaced")

    def test_failed_acceptance_leaves_original_and_reports_pending_saved_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, project, cli, source, library, preview = self.fixture(Path(tmp).resolve())
            before = tree_state(source)
            with patch("skillager.library.importing.set_trust", side_effect=OSError("acceptance unavailable")):
                code, result = self.invoke(project, cli, preview["next_command_argv"])
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "uncertain")
            self.assertEqual(result["save"]["status"], "uncertain")
            self.assertEqual(result["placement"]["status"], "not-started")
            self.assertEqual(tree_state(source), before)
            self.assertTrue((library / "skills/demo/SKILL.md").exists())
            status = fixture.checked(cli.run("library", "status", "lib/demo", "--json"))
            self.assertEqual(status["skill"]["acceptance"], "pending")

    def test_changed_original_after_save_retains_canonical_without_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, project, cli, source, library, preview = self.fixture(Path(tmp).resolve())
            original = import_placement.import_library_skill_locked

            def change_after_save(*args, **kwargs):
                result = original(*args, **kwargs)
                (source / "helper.sh").write_text("user changed the original")
                return result

            with patch.object(import_placement, "import_library_skill_locked", change_after_save):
                code, result = self.invoke(project, cli, preview["next_command_argv"])
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "saved-not-replaced")
            self.assertEqual(result["save"]["status"], "accepted")
            self.assertEqual(result["placement"]["status"], "not-applied")
            self.assertEqual((source / "helper.sh").read_text(), "user changed the original")
            self.assertEqual((library / "skills/demo/helper.sh").read_text(), "#!/bin/sh\ntrue\n")
            self.assertEqual(fixture.checked(cli.run("library", "status", "lib/demo", "--json"))["skill"]["acceptance"], "accepted")

    def test_publication_failure_retains_complete_original_recovery_and_saved_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, project, cli, source, library, preview = self.fixture(Path(tmp).resolve())
            before = tree_state(source)
            # Fail both installation and restoration at the actual filesystem
            # publisher. Its ordinary retained backup must survive the compound call.
            with patch("skillager.exposure.plan_apply._install", side_effect=OSError("publication unavailable")):
                code, result = self.invoke(project, cli, preview["next_command_argv"])
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["save"]["status"], "accepted")
            self.assertEqual(result["placement"]["status"], "partial")
            self.assertFalse(source.exists())
            recoveries = [item["recovery_path"] for item in result["placement"]["results"] if item["recovery_path"]]
            self.assertTrue(recoveries)
            self.assertEqual(tree_state(Path(recoveries[0])), before)
            self.assertEqual(tree_state(library / "skills/demo")["entries"], before["entries"])
            self.assertEqual(fixture.checked(cli.run("library", "status", "lib/demo", "--json"))["skill"]["acceptance"], "accepted")

    def test_lost_publisher_reply_keeps_uncertainty_and_can_be_observed_publicly(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, project, cli, source, _, preview = self.fixture(Path(tmp).resolve())
            original = import_placement.apply_plan_locked

            def lose_reply(*args):
                original(*args)
                raise OSError("lost exact publication reply")

            with patch.object(import_placement, "apply_plan_locked", lose_reply):
                code, result = self.invoke(project, cli, preview["next_command_argv"])
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "uncertain")
            self.assertEqual(result["save"]["status"], "accepted")
            self.assertEqual(result["placement"]["status"], "uncertain")
            inventory = fixture.checked(cli.run("list", "--scope", "project", "--json"))
            row = next(row for row in inventory["skills"] if row["skill_file"] == str(source / "SKILL.md"))
            self.assertEqual(row["kind"], "stub")
            repeated = fixture.checked(cli.run(*preview["next_command_argv"][1:]), 2)
            self.assertEqual(repeated["status"], "refused")

    def test_temporary_cleanup_failure_preserves_successful_stage_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, project, cli, _, _, preview = self.fixture(Path(tmp).resolve())
            original = tempfile.TemporaryDirectory.cleanup

            def fail_scratch_cleanup(directory):
                original(directory)
                if Path(directory.name).name.startswith("skillager-save-use-"):
                    raise OSError("scratch cleanup failed")

            with patch.object(tempfile.TemporaryDirectory, "cleanup", fail_scratch_cleanup):
                code, result = self.invoke(project, cli, preview["next_command_argv"])
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["save"]["status"], "accepted")
            self.assertEqual(result["placement"]["status"], "applied")
            self.assertEqual(result["cleanup"]["status"], "incomplete")

    def test_final_encoding_failure_is_a_missing_reply_not_a_no_effect_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture, project, cli, source, _, preview = self.fixture(Path(tmp).resolve())
            output, errors = io.StringIO(), io.StringIO()
            with patch("skillager.commands.import_placement.encode_result", side_effect=ValueError("response unavailable")), \
                    chdir(project), patch.dict(os.environ, cli.env), redirect_stdout(output), redirect_stderr(errors):
                code = main(preview["next_command_argv"][1:])
            self.assertEqual(code, 2)
            self.assertEqual(output.getvalue(), "")
            self.assertIn("response unavailable", errors.getvalue())
            inventory = fixture.checked(cli.run("list", "--scope", "project", "--json"))
            row = next(row for row in inventory["skills"] if row["skill_file"] == str(source / "SKILL.md"))
            self.assertEqual(row["kind"], "stub")


if __name__ == "__main__":
    unittest.main()
