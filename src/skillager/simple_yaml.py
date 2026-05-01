from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


class YamlError(ValueError):
    pass


def load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = loads(text)
    if not isinstance(data, dict):
        raise YamlError(f"{path} must contain a mapping")
    return data


def loads(text: str) -> Any:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise YamlError(str(exc)) from exc
    return {} if data is None else data


def dumps(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
