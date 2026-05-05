from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install built wheels together and run split-package smoke checks.")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist", help="Directory containing built wheels.")
    parser.add_argument("--python", help="Python version or executable for the smoke-test virtual environment.")
    args = parser.parse_args(argv)

    dist = args.dist.resolve()
    core_wheel = _single(dist.glob("skillager-*.whl"), "skillager wheel")
    linter_wheel = _single(dist.glob("skillager_linter-*.whl"), "skillager-linter wheel")

    with tempfile.TemporaryDirectory(prefix="skillager-wheelhouse-") as tmp:
        work = Path(tmp)
        venv = work / "venv"
        venv_cmd = ["uv", "venv"]
        if args.python:
            venv_cmd.extend(["--python", args.python])
        venv_cmd.append(str(venv))
        _run(venv_cmd)

        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        _run(["uv", "pip", "install", "--python", str(python), str(linter_wheel)])
        _run(["uv", "pip", "install", "--python", str(python), "--find-links", str(dist), str(core_wheel)])

        _run([str(python), "-m", "skillager", "--version"])
        _run([str(python), "-m", "skillager_linter", "--version"])

        project = work / "project"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname = "demo-project"\nversion = "0.0.0"\n',
            encoding="utf-8",
        )
        skill_dir = project / ".skills" / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Demo\n\nUse demo guidance.\n", encoding="utf-8")
        (skill_dir / "skillager.yaml").write_text(
            "schema: skillager.skill.v1\n"
            "audience:\n"
            "  - user\n"
            "  - dev\n"
            "activation:\n"
            "  default: manual\n",
            encoding="utf-8",
        )

        linter = _json([str(python), "-m", "skillager_linter", "--json", str(skill_dir)])
        _assert_finding(linter[0]["lint"]["findings"], "audience_both")

        env = os.environ.copy()
        state = work / "state"
        env.update({"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"})
        _run([str(python), "-m", "skillager", "index", "--no-packages"], cwd=project, env=env)
        listing = _json([str(python), "-m", "skillager", "list", "--json"], cwd=project, env=env)
        skill_id = _skill_id_for_root(listing, skill_dir, cwd=project)
        core = _json([str(python), "-m", "skillager", "lint", skill_id, "--json"], cwd=project, env=env)
        _assert_finding(core[0]["lint"]["findings"], "audience_both")

    return 0


def _single(paths: Iterable[Path], label: str) -> Path:
    selected = sorted(paths)
    if len(selected) != 1:
        raise SystemExit(f"expected exactly one {label}, found {len(selected)}")
    return selected[0]


def _run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd or ROOT, env=env, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise SystemExit(
            "command failed:\n"
            f"  {' '.join(command)}\n"
            f"  exit={completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return completed


def _json(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> object:
    completed = _run(command, cwd=cwd, env=env)
    return json.loads(completed.stdout)


def _skill_id_for_root(listing: object, skill_dir: Path, *, cwd: Path) -> str:
    if not isinstance(listing, list):
        raise SystemExit(f"expected skill list JSON array, got {type(listing).__name__}")
    target = skill_dir.resolve()
    matches: list[dict[str, object]] = []
    for skill in listing:
        if not isinstance(skill, dict):
            continue
        root = skill.get("root")
        if not isinstance(root, str):
            continue
        root_path = Path(root)
        if not root_path.is_absolute():
            root_path = cwd / root_path
        if root_path.resolve() == target:
            matches.append(skill)
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one indexed skill rooted at {target}, found {len(matches)}")
    skill_id = matches[0].get("id")
    if not isinstance(skill_id, str) or not skill_id:
        raise SystemExit(f"indexed skill at {target} is missing a string id")
    return skill_id


def _assert_finding(findings: list[dict[str, str]], code: str) -> None:
    if not any(finding.get("code") == code for finding in findings):
        raise SystemExit(f"expected finding {code}, got {findings}")


if __name__ == "__main__":
    raise SystemExit(main())
