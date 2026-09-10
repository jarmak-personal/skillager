from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skillager.commands.impl import _native_root_prefixes, _unmanaged_native_target
from skillager.state.approvals import snapshot
from skillager.trust import set_trust, trust_info


class InventoryPathTests(unittest.TestCase):
    def test_native_classification_preserves_component_boundaries_and_resolved_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            candidates = (
                (project / ".agents/skills/demo", "codex"),
                (project / ".agents/codex/skills/demo", "codex"),
                (project / ".codex/skills/demo", "codex"),
                (project / ".claude/skills/demo", "claude"),
                (project / ".agents/claude/skills/demo", "claude"),
                (project / ".agents/skills-extra/demo", None),
                (root / "project-other/.agents/skills/demo", None),
                (root / "external", None),
            )
            for path, _ in candidates:
                path.mkdir(parents=True)
                (path / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n")
            outside_alias = root / "native-alias"
            outside_alias.symlink_to(candidates[0][0], target_is_directory=True)
            inside_alias = project / ".agents/skills/external-alias"
            inside_alias.symlink_to(root / "external", target_is_directory=True)
            prefixes = _native_root_prefixes(project)
            for path, agent in (*candidates, (outside_alias, "codex"), (inside_alias, None)):
                with self.subTest(path=path):
                    target = _unmanaged_native_target({"root": str(path), "source": {"type": "collection"}}, native_prefixes=prefixes)
                    self.assertEqual(target.get("agent") if target else None, agent)
                    if target:
                        self.assertEqual(target["path"], str(path.resolve()))

    def test_approval_aliases_share_snapshot_and_invalidate_after_a_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            alias = root / "alias"
            set_trust(state, "project/demo", "reviewed", "hash", {})
            alias.symlink_to(state, target_is_directory=True)
            with snapshot([alias]):
                self.assertEqual(trust_info(alias, "project/demo", "hash")["state"], "reviewed")
                self.assertEqual(trust_info(state, "project/demo", "hash")["state"], "reviewed")
                set_trust(alias, "project/demo", "blocked", "hash", {})
                self.assertEqual(trust_info(state, "project/demo", "hash")["state"], "blocked")
                self.assertEqual(trust_info(alias, "project/demo", "hash")["state"], "blocked")
