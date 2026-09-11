from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skillager.commands.impl import _exposure_record, _exposure_removal_preview, _remove_confirmed_exposure
from skillager.exposure.drift import classify_exposure_target
from skillager.exposure.preview import removal_file_effects
from skillager.exposure.target_state import target_state_hash
from skillager.simple_yaml import dumps
from skillager.trust import content_hash


class ExposureRemovalPreviewTests(unittest.TestCase):
    def test_identity_swap_during_preview_refuses_mislabelled_effects(self) -> None:
        for boundary in ("classification", "effects"):
            for changed_field, changed_value in (("source_id", "team-a/b"), ("source_type", "skillager-stub")):
                with self.subTest(boundary=boundary, field=changed_field), tempfile.TemporaryDirectory() as tmp:
                    target = Path(tmp) / "team-a-b"
                    target.mkdir()
                    (target / "SKILL.md").write_text("Reviewed fixture.\n", encoding="utf-8")
                    sidecar = target / "skillager.materialized.yaml"
                    state = {
                        "schema": "skillager.materialized.v1", "source_type": "project",
                        "source_id": "team/a/b", "materialized_hash": content_hash(target),
                    }
                    sidecar.write_text(dumps(state), encoding="utf-8")
                    item = _exposure_record(sidecar, state, fallback_agent="codex", fallback_scope="project")
                    self.assertIsNotNone(item)
                    replacement = dumps({**state, changed_field: changed_value})

                    def swap_during_classification(*args, **kwargs):
                        sidecar.write_text(replacement, encoding="utf-8")
                        return classify_exposure_target(*args, **kwargs)

                    def swap_during_effects(*args, **kwargs):
                        sidecar.write_text(replacement, encoding="utf-8")
                        return target_state_hash(*args, **kwargs)

                    seam = "classify_exposure_target" if boundary == "classification" else "target_state_hash"
                    swap = swap_during_classification if boundary == "classification" else swap_during_effects
                    with patch(f"skillager.commands.impl.{seam}", side_effect=swap):
                        with self.assertRaisesRegex(ValueError, "changed during removal preview"):
                            _exposure_removal_preview(item, target=target, force=False, json_output=True)
                    self.assertEqual(sidecar.read_text(), replacement)
                    self.assertEqual((target / "SKILL.md").read_text(), "Reviewed fixture.\n")
                    self.assertFalse(any(target.parent.glob(".team-a-b.skillager-remove-*")))

    def test_root_permission_change_during_detach_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "managed"
            target.mkdir(mode=0o755)
            target.chmod(0o755)
            (target / "SKILL.md").write_text("Reviewed fixture.\n", encoding="utf-8")
            expected = removal_file_effects(target, target_hash=target_state_hash(target))
            original_replace = os.replace

            def change_detached_permissions(source, destination):
                original_replace(source, destination)
                if Path(source) == target:
                    Path(destination).chmod(0o700)

            with patch("skillager.commands.impl.os.replace", side_effect=change_detached_permissions):
                with self.assertRaisesRegex(ValueError, "changed during removal"):
                    _remove_confirmed_exposure(target, expected_preview=expected)
            self.assertEqual(target.stat().st_mode & 0o7777, 0o700)
            self.assertEqual((target / "SKILL.md").read_text(), "Reviewed fixture.\n")
            self.assertFalse(any(target.parent.glob(".managed.skillager-remove-*")))

    def test_matching_legacy_hash_does_not_allow_an_incomplete_effect_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "managed"
            target.mkdir()
            (target / "SKILL.md").write_text("Reviewed fixture.\n", encoding="utf-8")
            (target / "support.txt").write_text("Supporting instructions.\n", encoding="utf-8")
            expected = removal_file_effects(target, target_hash=target_state_hash(target))
            expected["file_effects"] = [item for item in expected["file_effects"] if item["path"] != "support.txt"]
            with self.assertRaisesRegex(ValueError, "changed during removal"):
                _remove_confirmed_exposure(target, expected_preview=expected)
            self.assertEqual((target / "support.txt").read_text(), "Supporting instructions.\n")
            self.assertEqual(target_state_hash(target), expected["target_state_hash"])
            self.assertFalse(any(target.parent.glob(".managed.skillager-remove-*")))
