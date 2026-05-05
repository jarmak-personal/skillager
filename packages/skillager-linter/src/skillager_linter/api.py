from __future__ import annotations

from pathlib import Path
from typing import Any

from .findings import lint_skill, to_lint_report
from .models import LintResult
from .scan_paths import iter_lint_targets
from .simple_yaml import YamlError, load_manifest_mapping
from .validators import (
    ManifestValidationError,
    canonical_entrypoint,
    find_manifest,
    infer_id,
    schema_findings,
    validate_skill_metadata,
)


def lint_skill_root(path: Path, *, source: dict[str, Any] | None = None) -> LintResult:
    root = path.resolve()
    source_data = _source(source)
    manifest = find_manifest(root)
    raw_manifest = None
    if manifest:
        try:
            raw_manifest = load_manifest_mapping(manifest)
        except YamlError as exc:
            return _error_result(root, manifest, source_data, exc)
    try:
        skill_text = _read_skill_text(root)
    except ManifestValidationError as exc:
        return _error_result(root, manifest, source_data, exc)
    return validate_skill_data(
        raw_manifest,
        root=root,
        manifest_path=manifest,
        skill_text=skill_text,
        source=source_data,
        inferred=raw_manifest is None,
    )


def lint_manifest(path: Path, *, skill_root: Path | None = None, source: dict[str, Any] | None = None) -> LintResult:
    manifest = path.resolve()
    root = (skill_root or manifest.parent).resolve()
    source_data = _source(source)
    try:
        raw_manifest = load_manifest_mapping(manifest)
    except YamlError as exc:
        return _error_result(root, manifest, source_data, exc)
    try:
        skill_text = _read_skill_text(root)
    except ManifestValidationError as exc:
        return _error_result(root, manifest, source_data, exc)
    return validate_skill_data(
        raw_manifest,
        root=root,
        manifest_path=manifest,
        skill_text=skill_text,
        source=source_data,
        inferred=False,
    )


def validate_skill_data(
    raw_manifest: dict[str, Any] | None,
    *,
    root: Path,
    manifest_path: Path | None,
    skill_text: str,
    source: dict[str, Any] | None = None,
    inferred: bool = False,
) -> LintResult:
    source_data = _source(source)
    result_path = root.resolve()
    try:
        metadata = validate_skill_metadata(
            raw_manifest,
            root=root,
            manifest_path=manifest_path,
            skill_text=skill_text,
            source=source_data,
            inferred=inferred,
        )
    except ManifestValidationError as exc:
        return _error_result(result_path, manifest_path.resolve() if manifest_path else None, source_data, exc)
    lint = lint_skill(metadata)
    return LintResult(
        path=result_path,
        manifest_path=metadata.manifest_path,
        skill_id=metadata.skill_id,
        lint=to_lint_report(list(lint.get("findings") or [])),
        metadata=metadata,
    )


def lint_paths(paths: list[Path], *, recursive: bool = True) -> list[LintResult]:
    selected = paths or [Path.cwd()]
    results: list[LintResult] = []
    for path in selected:
        for target in iter_lint_targets(path, recursive=recursive):
            if target.is_file() and target.name == "skillager.yaml":
                results.append(lint_manifest(target))
            elif target.is_dir():
                results.append(lint_skill_root(target))
    return results


def _read_skill_text(root: Path) -> str:
    return canonical_entrypoint(root).read_text(encoding="utf-8", errors="replace")


def _error_result(path: Path, manifest_path: Path | None, source: dict[str, Any], exc: BaseException) -> LintResult:
    try:
        skill_id = infer_id(path, source)
    except ManifestValidationError:
        skill_id = None
    return LintResult(
        path=path.resolve(),
        manifest_path=manifest_path.resolve() if manifest_path else None,
        skill_id=skill_id,
        lint=to_lint_report(schema_findings(exc)),
        metadata=None,
    )


def _source(source: dict[str, Any] | None) -> dict[str, Any]:
    return dict(source or {"type": "local"})
