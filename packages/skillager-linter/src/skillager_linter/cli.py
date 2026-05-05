from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .api import lint_paths
from .findings import blocking_findings
from .models import LintResult
from .templates import MINIMAL_MANIFEST_YAML


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return run(args, parser)
    except BrokenPipeError:
        return 0
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    except Exception as exc:
        print(f"skillager-lint: error: {exc}", file=sys.stderr)
        return 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillager-lint",
        description="Lint Skillager skill manifests without loading trust, state, or skill bodies into output.",
    )
    parser.add_argument("paths", nargs="*", type=Path, help="Skill roots, skillager.yaml files, or directories to scan.")
    parser.add_argument("--json", action="store_true", help="Emit lint results as JSON.")
    parser.add_argument("--print-minimal-manifest", action="store_true", help="Print a minimal valid skillager.yaml.")
    parser.add_argument("--version", action="version", version=f"skillager-linter {__version__}")
    return parser


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.print_minimal_manifest:
        if args.paths or args.json:
            parser.error("--print-minimal-manifest cannot be combined with paths or --json")
        print(MINIMAL_MANIFEST_YAML, end="")
        return 0

    paths = args.paths or [Path.cwd()]
    for path in paths:
        if not path.exists():
            parser.error(f"path does not exist: {path}")
        if path.is_file() and path.name != "skillager.yaml":
            parser.error(f"manifest file must be named skillager.yaml: {path}")

    results = lint_paths(paths)
    if args.json:
        print(json.dumps([result.to_dict() for result in results], indent=2))
    else:
        _print_plain(results)
    return 1 if any(blocking_findings(result.lint.to_dict()) for result in results) else 0


def _print_plain(results: list[LintResult]) -> None:
    reports = [result for result in results if result.lint.status != "ok"]
    if not reports:
        print("No manifest lint findings.")
        return
    for result in reports:
        lint = result.lint
        skill_id = result.skill_id or "<unknown>"
        print(f"{result.path}: lint={lint.status} skill_id={skill_id}")
        for item in lint.findings:
            print(f"  {item.severity} {item.code} {item.field}: {item.detail} ({item.rule_key})")
