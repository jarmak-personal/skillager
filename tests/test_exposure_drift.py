from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skillager.exposure import drift as drift_impl
from skillager.exposure.drift import classify_exposure_target, scan_project_exposures
from skillager.simple_yaml import dumps, load_mapping
from skillager.skills.tree import content_tree_fingerprint
from skillager.trust import content_hash


BODY_SENTINEL = "PRIVATE DRIFT BODY MUST NOT LEAK"


def write_target(root: Path, body: str = f"# Demo\n\n{BODY_SENTINEL}\n") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(body, encoding="utf-8")


def write_sidecar(root: Path, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "schema": "skillager.materialized.v1",
        "id": "community/demo",
        "source_id": "community/demo",
        "source_type": "collection",
        "source_package": "community",
        "source_hash": "source-hash",
        "materialized_hash": content_hash(root),
        "materialized_fingerprint": content_tree_fingerprint(root),
        "agent": "codex",
        "scope": "project",
    }
    data.update(overrides)
    (root / "skillager.materialized.yaml").write_text(dumps(data), encoding="utf-8")
    return data


class ExposureDriftTests(unittest.TestCase):

    def test_current_fingerprint_hit_still_requires_full_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "demo"
            write_target(target)
            write_sidecar(target)

            original_hash = drift_impl.content_hash
            with patch.object(drift_impl, "content_hash", wraps=original_hash) as hash_mock:
                record = classify_exposure_target(target)

            assert record is not None
            self.assertEqual(record["status"], "current")
            self.assertNotIn("ownership", record)
            self.assertEqual(hash_mock.call_count, 1)

    def test_working_scan_rehashes_instead_of_authorizing_with_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            target = project / ".agents" / "skills" / "demo"
            write_target(target)
            write_sidecar(target)

            original_hash = drift_impl.content_hash
            with patch.object(drift_impl, "content_hash", wraps=original_hash) as hash_mock:
                changes = scan_project_exposures(project, agent="codex")

            self.assertEqual(changes["current"], 1)
            self.assertEqual(changes["items"], [])
            self.assertEqual(hash_mock.call_count, 1)

    def test_legacy_keep_local_metadata_does_not_hide_a_local_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "demo"
            write_target(target)
            write_sidecar(target)
            (target / "SKILL.md").write_text("# Edited\n\nProject-local workflow.\n", encoding="utf-8")

            edited = classify_exposure_target(target)
            assert edited is not None
            self.assertEqual(edited["status"], "local_edit")

            sidecar = load_mapping(target / "skillager.materialized.yaml")
            sidecar.update(
                {
                    "customized": True,
                    "customization_decision": "keep-local",
                    "customized_hash": content_hash(target),
                    "customized_fingerprint": content_tree_fingerprint(target),
                }
            )
            (target / "skillager.materialized.yaml").write_text(dumps(sidecar), encoding="utf-8")
            kept = classify_exposure_target(target)
            assert kept is not None
            self.assertEqual(kept["status"], "local_edit")

            (target / "SKILL.md").write_text("# Edited Again\n\nA newer local workflow.\n", encoding="utf-8")
            edited_again = classify_exposure_target(target)
            assert edited_again is not None
            self.assertEqual(edited_again["status"], "local_edit")

    def test_mtime_only_change_invalidates_exposure_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "demo"
            write_target(target)
            write_sidecar(target)
            skill_file = target / "SKILL.md"
            stat = skill_file.stat()
            os.utime(skill_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

            original_hash = drift_impl.content_hash
            with patch.object(drift_impl, "content_hash", wraps=original_hash) as hash_mock:
                record = classify_exposure_target(target)

            assert record is not None
            self.assertEqual(record["status"], "current")
            self.assertEqual(hash_mock.call_count, 1)

    def test_same_size_edit_with_restored_mtime_is_still_a_local_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "demo"
            write_target(target)
            write_sidecar(target)
            skill_file = target / "SKILL.md"
            original_stat = skill_file.stat()
            original = skill_file.read_text(encoding="utf-8")
            edited = original.replace("Demo", "Dome", 1)
            self.assertEqual(len(edited.encode()), len(original.encode()))
            skill_file.write_text(edited, encoding="utf-8")
            os.utime(skill_file, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

            record = classify_exposure_target(target)

            assert record is not None
            self.assertEqual(record["status"], "local_edit")

    def test_legacy_sidecar_treats_excluded_target_entry_as_local_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "demo"
            write_target(target)
            sidecar = write_sidecar(target)
            self.assertNotIn("materialized_target_hash", sidecar)
            (target / "draft.tmp").write_bytes(b"LOCAL EXCLUDED CONTENT\n")

            record = classify_exposure_target(target)

            assert record is not None
            self.assertEqual(record["status"], "local_edit")

    def test_target_missing_blocked_and_sidecar_error_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            missing = root / "missing"
            write_target(missing)
            write_sidecar(missing)
            (missing / "SKILL.md").unlink()
            missing_record = classify_exposure_target(missing)
            assert missing_record is not None
            self.assertEqual(missing_record["status"], "target_missing")

            blocked = root / "blocked"
            write_target(blocked)
            write_sidecar(blocked)
            (blocked / "SKILL.md").write_text("# Blocked\n\nUnexpected local bytes.\n", encoding="utf-8")
            blocked_hash = content_hash(blocked)
            sidecar = load_mapping(blocked / "skillager.materialized.yaml")
            sidecar["exposure_blocked_hashes"] = [blocked_hash]
            (blocked / "skillager.materialized.yaml").write_text(dumps(sidecar), encoding="utf-8")
            blocked_record = classify_exposure_target(blocked)
            assert blocked_record is not None
            self.assertEqual(blocked_record["status"], "blocked")

            malformed = root / "malformed"
            write_target(malformed)
            (malformed / "skillager.materialized.yaml").write_text("[unterminated\n", encoding="utf-8")
            malformed_record = classify_exposure_target(malformed)
            assert malformed_record is not None
            self.assertEqual(malformed_record["status"], "sidecar_error")

    def test_scan_reports_unmanaged_actionable_items_without_body_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            managed = project / ".agents" / "skills" / "managed"
            unmanaged = project / ".agents" / "skills" / "unmanaged"
            write_target(managed)
            write_sidecar(managed)
            write_target(unmanaged)

            changes = scan_project_exposures(project, agent="codex")

            self.assertEqual(changes["current"], 1)
            self.assertEqual(changes["unmanaged"], 1)
            self.assertEqual([item["status"] for item in changes["items"]], ["unmanaged"])
            self.assertNotIn("ownership", changes["items"][0])
            self.assertNotIn(BODY_SENTINEL, json.dumps(changes))

    def test_deleted_target_is_not_invented_without_a_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            target = project / ".agents" / "skills" / "deleted"
            write_target(target)
            write_sidecar(target)
            shutil.rmtree(target)

            changes = scan_project_exposures(project, agent="codex")

            self.assertEqual(changes["items"], [])
            self.assertNotIn("fully_deleted_targets", changes)


if __name__ == "__main__":
    unittest.main()
