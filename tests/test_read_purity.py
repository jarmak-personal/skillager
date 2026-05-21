from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from support import chdir
from skillager.cli import main


def write_skill(root: Path, body: str = "# Demo\n\nUse demo guidance.\n") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(body, encoding="utf-8")


def snapshot_tree(*roots: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for root in roots:
        if not root.exists():
            snapshot[str(root)] = "<missing>"
            continue
        for path in sorted(root.rglob("*")):
            rel = f"{root.name}/{path.relative_to(root)}"
            if path.is_dir():
                snapshot[rel] = "<dir>"
            elif path.is_symlink():
                snapshot[rel] = f"<symlink:{os.readlink(path)}>"
            else:
                snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


class SkillagerReadPurityTests(unittest.TestCase):

    def test_metadata_commands_do_not_write_state_cache_or_project_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            catalog = root / "catalog"
            cache = root / "cache"
            project = root / "project"
            write_skill(project / ".skills" / "demo")
            env = {
                "SKILLAGER_STATE_DIR": str(state),
                "SKILLAGER_CATALOG_STATE_DIR": str(catalog),
                "SKILLAGER_CACHE_DIR": str(cache),
                "NO_COLOR": "1",
            }
            with (
                patch.dict(os.environ, env),
                patch("skillager.discovery.find_project_root", return_value=project),
                patch("pathlib.Path.home", return_value=root),
                chdir(project),
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--agent", "codex", "--no-packages"]), 0)
                self.assertEqual(main(["tag", "create", "demo"]), 0)
                self.assertEqual(main(["tag", "add", "demo", "project/demo"]), 0)
                self.assertEqual(main(["project", "attach-tag", "demo"]), 0)
            (state / "index.json").unlink()

            before = snapshot_tree(state, catalog, cache, project)
            commands = [
                ["working", "--agent", "codex", "--json"],
                ["status", "--agent", "codex", "--no-packages", "--json"],
                ["handoff", "--agent", "codex", "--json"],
                ["list", "--no-packages", "--json"],
                ["search", "demo", "--agent", "codex", "--json"],
                ["show", "project/demo", "--json"],
                ["tag", "show", "demo", "--json"],
                ["project", "tags", "--json"],
                ["doctor", "--agent", "codex", "--no-packages", "--json"],
                ["lint", "--json"],
            ]
            with (
                patch.dict(os.environ, env),
                patch("skillager.discovery.find_project_root", return_value=project),
                patch("pathlib.Path.home", return_value=root),
                chdir(project),
            ):
                for command in commands:
                    output = StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(main(command), 0, command)
                    if command[0] in {"working", "status", "handoff", "list", "search", "show", "tag", "project", "doctor", "lint"}:
                        json.loads(output.getvalue())

            self.assertEqual(snapshot_tree(state, catalog, cache, project), before)
            self.assertFalse((state / "index.json").exists())


if __name__ == "__main__":
    unittest.main()
