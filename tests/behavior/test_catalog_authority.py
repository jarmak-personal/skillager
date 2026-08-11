from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.behavior.support import BODY_SENTINEL, CliResult, make_basic_workspace


class CatalogAuthorityBehaviorTests(unittest.TestCase):
    def assert_code(self, result: CliResult, expected: int) -> None:
        self.assertEqual(
            result.code,
            expected,
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}",
        )

    def test_project_metadata_cannot_replace_user_catalog_or_approval_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            cli.env.pop("SKILLAGER_CATALOG_STATE_DIR")
            cli.env["XDG_CONFIG_HOME"] = str(root / "user-config")

            user_library = root / "user-library"
            fake_catalog = cli.project / ".attacker-catalog"
            fake_library = cli.project / "fake-library"
            self.assert_code(cli.run("library", "init", "--path", str(user_library), "--no-git"), 0)
            self.assert_code(
                cli.run(
                    "--catalog-state-dir",
                    str(fake_catalog),
                    "library",
                    "init",
                    "--path",
                    str(fake_library),
                    "--no-git",
                ),
                0,
            )
            created = cli.run(
                "--catalog-state-dir",
                str(fake_catalog),
                "library",
                "new",
                "poison",
                "--json",
            )
            self.assert_code(created, 0)
            poison = fake_library / "skills" / "poison" / "SKILL.md"
            poison.write_text(f"# Poison\n\n{BODY_SENTINEL}\n", encoding="utf-8")
            preview = cli.run(
                "--catalog-state-dir",
                str(fake_catalog),
                "library",
                "accept",
                "lib/poison",
                "--json",
            )
            self.assert_code(preview, 0)
            accept_args = preview.json()["next_command_argv"][1:]
            accepted = cli.run("--catalog-state-dir", str(fake_catalog), *accept_args, "--json")
            self.assert_code(accepted, 0)

            metadata = cli.project / ".skillager" / "tags.json"
            metadata.parent.mkdir(parents=True)
            metadata.write_text(
                json.dumps(
                    {
                        "schema": "skillager.project-tags.v1",
                        "catalog_state_dir": ".attacker-catalog",
                        "tags": {},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            status = cli.run("library", "status", "--json")
            self.assert_code(status, 0)
            self.assertEqual(status.json()["library"]["root"], str(user_library.resolve()))

            blocked = cli.run("show", "lib/poison", "--content")
            self.assert_code(blocked, 2)
            self.assertNotIn(BODY_SENTINEL, blocked.stdout)
            self.assertNotIn(BODY_SENTINEL, blocked.stderr)


if __name__ == "__main__":
    unittest.main()
