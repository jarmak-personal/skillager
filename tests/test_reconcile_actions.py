from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from skillager.exposure.reconcile import (
    keep_local,
    keep_local_preview,
    quarantine,
    quarantine_preview,
)
from skillager.simple_yaml import dumps, load_mapping
from skillager.skills.tree import content_tree_fingerprint
from skillager.trust import content_hash


def managed_target(project: Path, name: str = "demo") -> Path:
    target = project / ".agents" / "skills" / name
    target.mkdir(parents=True)
    target.joinpath("SKILL.md").write_text("# Original\n\nReviewed exposure.\n", encoding="utf-8")
    digest = content_hash(target)
    target.joinpath("skillager.materialized.yaml").write_text(
        dumps(
            {
                "schema": "skillager.materialized.v1",
                "id": "community/demo",
                "source_id": "community/demo",
                "source_type": "collection",
                "source_hash": digest,
                "materialized_hash": digest,
                "materialized_fingerprint": content_tree_fingerprint(target),
                "agent": "codex",
                "scope": "project",
                "customized": False,
                "ownership": "external",
            }
        ),
        encoding="utf-8",
    )
    return target


class ReconcileActionTests(unittest.TestCase):
    def test_keep_local_rehashes_after_preview_and_refuses_stale_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            catalog = root / "catalog"
            target = managed_target(project)
            target.joinpath("SKILL.md").write_text("# First Edit\n\nPreviewed bytes.\n", encoding="utf-8")
            preview = keep_local_preview(project, catalog, "community/demo", agent="codex")
            target.joinpath("SKILL.md").write_text("# Second Edit\n\nChanged after preview.\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "changed since preview"):
                keep_local(
                    project,
                    catalog,
                    "community/demo",
                    expected_hash=preview["expected_current_hash"],
                    agent="codex",
                )

            sidecar = load_mapping(target / "skillager.materialized.yaml")
            self.assertFalse(sidecar["customized"])
            self.assertNotIn("customized_hash", sidecar)

    def test_quarantine_rehashes_after_preview_without_moving_newer_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            catalog = root / "catalog"
            target = managed_target(project)
            target.joinpath("SKILL.md").write_text("# First Edit\n\nPreviewed bytes.\n", encoding="utf-8")
            preview = quarantine_preview(project, catalog, "community/demo", agent="codex")
            newer = "# Newer Edit\n\nMust remain in place.\n"
            target.joinpath("SKILL.md").write_text(newer, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "changed since quarantine preview"):
                quarantine(
                    project,
                    catalog,
                    "community/demo",
                    expected_hash=preview["expected_current_hash"],
                    agent="codex",
                )

            self.assertEqual(target.joinpath("SKILL.md").read_text(encoding="utf-8"), newer)
            self.assertFalse((project / ".skillager-quarantine").exists())

    def test_two_quarantines_serialize_and_preserve_one_recoverable_copy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            catalog = root / "catalog"
            target = managed_target(project)
            target.joinpath("SKILL.md").write_text("# Concurrent Edit\n\nPreserve exactly once.\n", encoding="utf-8")
            expected_hash = content_hash(target)
            barrier = threading.Barrier(3)
            results: list[dict] = []
            errors: list[Exception] = []

            def worker() -> None:
                barrier.wait()
                try:
                    results.append(
                        quarantine(
                            project,
                            catalog,
                            "community/demo",
                            expected_hash=expected_hash,
                            agent="codex",
                        )
                    )
                except Exception as exc:  # The losing writer must fail closed.
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=10)

            self.assertEqual(len(results), 1)
            self.assertEqual(len(errors), 1)
            quarantine_path = Path(results[0]["quarantine_path"])
            self.assertEqual(content_hash(quarantine_path), expected_hash)
            self.assertFalse(target.joinpath("SKILL.md").exists())
            self.assertEqual(
                len(list((project / ".skillager-quarantine" / "exposures").iterdir())),
                1,
            )


if __name__ == "__main__":
    unittest.main()
