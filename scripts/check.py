from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local Skillager quality gates.")
    parser.add_argument("--skip-build", action="store_true", help="Skip uv build in the full check.")
    parser.add_argument("--python", default="3.13", help="Python version for uv run commands. Defaults to 3.13.")
    args = parser.parse_args(argv)

    env = os.environ.copy()
    env.setdefault("TMPDIR", _clean_tmpdir())

    commands = [
        ("ruff", uv_run(args.python, "ruff", "check", ".")),
        ("mypy", uv_run(args.python, "mypy", "src/skillager")),
        ("tests", uv_run(args.python, "python", "-m", "unittest", "discover", "-s", "tests")),
        ("module entrypoint", uv_run(args.python, "python", "-m", "skillager", "--version")),
    ]
    if not args.skip_build:
        commands.append(("build", ["uv", "build"]))
    commands.append(("whitespace", ["git", "diff", "--check"]))

    for label, command in commands:
        print(f"==> {label}: {' '.join(command)}", flush=True)
        completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
        if completed.returncode:
            return completed.returncode
    return 0


def uv_run(python: str | None, *args: str) -> list[str]:
    command = ["uv", "run"]
    if python:
        command.extend(["--python", python])
    command.extend(args)
    return command


def _clean_tmpdir() -> str:
    candidate = Path("/var/tmp")
    if candidate.is_dir() and not (candidate / ".git").exists() and not (candidate / "pyproject.toml").exists():
        return str(candidate)
    return "/tmp"


if __name__ == "__main__":
    raise SystemExit(main())
