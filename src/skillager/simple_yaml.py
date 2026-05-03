from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

MAX_MANIFEST_BYTES = 32_000


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


class StrictYamlError(YamlError):
    pass


class _StrictManifestLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _StrictManifestLoader, node: yaml.nodes.MappingNode, deep: bool = False) -> dict[str, Any]:
    if node.tag != "tag:yaml.org,2002:map":
        raise StrictYamlError("manifest mappings must use plain YAML mapping syntax")
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise StrictYamlError("manifest mapping keys must be strings")
        if key == "<<":
            raise StrictYamlError("manifest merge keys are not allowed")
        if key in result:
            raise StrictYamlError("duplicate manifest key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


def _construct_undefined(loader: _StrictManifestLoader, node: yaml.Node) -> Any:
    raise StrictYamlError("custom YAML tags are not allowed in skillager.yaml")


def _compose_node(loader: _StrictManifestLoader, parent: yaml.Node | None, index: int | None) -> yaml.Node:
    if loader.check_event(yaml.AliasEvent):
        raise StrictYamlError("YAML aliases are not allowed in skillager.yaml")
    node = yaml.SafeLoader.compose_node(loader, parent, index)
    if getattr(node, "anchor", None):
        raise StrictYamlError("YAML anchors are not allowed in skillager.yaml")
    return node


_StrictManifestLoader.add_constructor("tag:yaml.org,2002:map", _construct_mapping)
_StrictManifestLoader.add_constructor(None, _construct_undefined)
_StrictManifestLoader.compose_node = _compose_node  # type: ignore[method-assign]


def load_manifest_mapping(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) > MAX_MANIFEST_BYTES:
        raise StrictYamlError(f"{path.name} is larger than {MAX_MANIFEST_BYTES} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StrictYamlError("skillager.yaml must be valid UTF-8") from exc
    try:
        docs = list(yaml.load_all(text, Loader=_StrictManifestLoader))
    except yaml.YAMLError as exc:
        raise StrictYamlError("skillager.yaml contains invalid YAML") from exc
    if len(docs) != 1:
        raise StrictYamlError("skillager.yaml must contain exactly one YAML document")
    document = docs[0]
    if not isinstance(document, dict):
        raise StrictYamlError("skillager.yaml must contain a mapping")
    return document


def dumps(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
