from __future__ import annotations

import json
import re
import shlex
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from .support import BODY_SENTINEL, REPO_ROOT, SkillagerCli, make_basic_workspace, write_basic_skill


MARKER_RE = re.compile(r"^<!--\s*skillager-test(?P<attrs>.*?)\s*-->$")
DOC_PATHS = [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").glob("*.md"))]


@dataclass(frozen=True)
class DocsExample:
    path: Path
    line: int
    fixture: str
    commands: list[str]


class DocsExampleBehaviorTests(unittest.TestCase):
    def test_marked_docs_examples_execute_successfully(self) -> None:
        examples = list(iter_docs_examples(DOC_PATHS))
        self.assertGreater(len(examples), 0, "expected at least one marked docs example")
        for example in examples:
            with self.subTest(path=str(example.path.relative_to(REPO_ROOT)), line=example.line):
                self.run_example(example)

    def run_example(self, example: DocsExample) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            cli = make_fixture(example.fixture, Path(tmp_name))
            for command in example.commands:
                result = run_docs_command(cli, command)
                self.assertEqual(
                    result.code,
                    0,
                    f"{example.path}:{example.line}: {command}\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}",
                )
                self.assertNotIn(BODY_SENTINEL, result.stdout)
                self.assertNotIn(BODY_SENTINEL, result.stderr)
                args = shlex.split(command)
                if any(arg.endswith("json") for arg in args):
                    json.loads(result.stdout)


def iter_docs_examples(paths: list[Path]):
    for path in paths:
        yield from iter_docs_examples_from_text(path, path.read_text(encoding="utf-8"))


def iter_docs_examples_from_text(path: Path, text: str):
    lines = text.splitlines()
    pending: tuple[int, dict[str, str]] | None = None
    in_fence = False
    fence_marker = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if in_fence:
            if stripped.startswith(fence_marker):
                in_fence = False
            index += 1
            continue
        marker_match = MARKER_RE.match(stripped)
        if marker_match:
            pending = (index + 1, parse_attrs(marker_match.group("attrs").strip()))
            index += 1
            continue
        if stripped.startswith(("```", "~~~")):
            fence_marker = stripped[:3]
            language = stripped[3:].strip().split(None, 1)[0] if stripped[3:].strip() else ""
            if pending and language in {"bash", "sh", "shell"}:
                start_line, attrs = pending
                commands: list[str] = []
                index += 1
                while index < len(lines) and not lines[index].strip().startswith(fence_marker):
                    command = lines[index].strip()
                    if command and not command.startswith("#"):
                        commands.append(command)
                    index += 1
                pending = None
                yield DocsExample(
                    path=path,
                    line=start_line,
                    fixture=attrs.get("fixture", "basic_project"),
                    commands=commands,
                )
                index += 1
                continue
            pending = None
            in_fence = True
            index += 1
            continue
        if pending and stripped:
            pending = None
        index += 1


def parse_attrs(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for token in shlex.split(raw):
        if "=" not in token:
            raise ValueError(f"invalid skillager-test token: {token}")
        key, value = token.split("=", 1)
        if key != "fixture":
            raise ValueError(f"unknown skillager-test attribute: {key}")
        attrs[key] = value
    return attrs


def make_fixture(name: str, tmp: Path) -> SkillagerCli:
    if name != "basic_project":
        raise ValueError(f"unknown docs example fixture: {name}")
    project, cli = make_basic_workspace(tmp)
    write_basic_skill(project)
    return cli


def run_docs_command(cli: SkillagerCli, command: str):
    args = shlex.split(command)
    if not args:
        raise ValueError("empty docs command")
    blocked_tokens = {"|", "&&", "||", ";", ">", ">>", "<"}
    if any(token in blocked_tokens for token in args):
        raise ValueError(f"docs test command uses unsupported shell syntax: {command}")
    if "$(" in command or "`" in command:
        raise ValueError(f"docs test command uses unsupported shell substitution: {command}")
    if args[0] == "skillager":
        return cli.run(*args[1:])
    if args[:3] == ["python", "-m", "skillager"]:
        return cli.run(*args[3:])
    raise ValueError(f"docs test command must start with skillager or python -m skillager: {command}")


if __name__ == "__main__":
    unittest.main()
