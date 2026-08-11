from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
BODY_SENTINEL = "FULL_BODY_SENTINEL_DO_NOT_LEAK"


@dataclass(frozen=True)
class CliResult:
    code: int
    stdout: str
    stderr: str

    def json(self):
        return json.loads(self.stdout)


class SkillagerCli:
    def __init__(self, project: Path, *, state: Path, catalog_state: Path, home: Path, cache: Path) -> None:
        self.project = project
        env = os.environ.copy()
        python_path = str(SRC_ROOT)
        if env.get("PYTHONPATH"):
            python_path = f"{python_path}{os.pathsep}{env['PYTHONPATH']}"
        env.update(
            {
                "HOME": str(home),
                "NO_COLOR": "1",
                "PYTHONPATH": python_path,
                "SKILLAGER_CACHE_DIR": str(cache),
                "SKILLAGER_CATALOG_STATE_DIR": str(catalog_state),
                "SKILLAGER_NO_UPDATE_CHECK": "1",
                "SKILLAGER_STATE_DIR": str(state),
            }
        )
        self.env = env

    def run(self, *args: str) -> CliResult:
        completed = subprocess.run(
            [sys.executable, "-m", "skillager", *args],
            cwd=self.project,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
        )
        return CliResult(completed.returncode, completed.stdout, completed.stderr)

    def run_confirmed(self, *args: str) -> CliResult:
        """Preview a mutation and execute the exact bound command it returns."""
        values = list(args)
        if "--yes" not in values:
            raise ValueError("run_confirmed requires --yes in the requested command")
        values.remove("--yes")
        wants_json = "--json" in values
        preview_values = values if wants_json else [*values, "--json"]
        preview = self.run(*preview_values)
        if preview.code != 0:
            return preview
        payload = preview.json()
        command = list(payload.get("next_command_argv") or [])
        if not command or command[0] != "skillager":
            return preview
        confirmed = command[1:]
        if wants_json and "--json" not in confirmed:
            confirmed.append("--json")
        return self.run(*confirmed)


def make_basic_workspace(tmp: Path) -> tuple[Path, SkillagerCli]:
    project = tmp / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname = \"demo\"\n", encoding="utf-8")
    cli = SkillagerCli(
        project,
        state=tmp / "state" / "project",
        catalog_state=tmp / "state" / "catalog",
        home=tmp / "home",
        cache=tmp / "cache",
    )
    return project, cli


def write_basic_skill(project: Path, slug: str = "gis-domain") -> Path:
    skill = project / ".skills" / slug
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "# GIS Domain\n\n"
        "Use spatial indexing guidance for projected coordinate systems.\n\n"
        f"{BODY_SENTINEL}\n",
        encoding="utf-8",
    )
    return skill
