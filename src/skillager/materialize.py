from __future__ import annotations

import sys
import types

from .exposure import impl as _impl

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)

AGENT_NOTE = _impl.AGENT_NOTE
TRUSTED_STATES = _impl.TRUSTED_STATES
WORKING_SKILL_ID = _impl.WORKING_SKILL_ID
agent_note_paths = _impl.agent_note_paths
materialize_router = _impl.materialize_router
materialize_skills = _impl.materialize_skills
materialize_working_skill = _impl.materialize_working_skill
render_working_skill = _impl.render_working_skill
target_dir = _impl.target_dir
working_source_hash = _impl.working_source_hash


def __getattr__(name: str):
    return getattr(_impl, name)


class _FacadeModule(types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(_impl, name):
            setattr(_impl, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _FacadeModule


__all__ = [
    "AGENT_NOTE",
    "TRUSTED_STATES",
    "WORKING_SKILL_ID",
    "agent_note_paths",
    "materialize_router",
    "materialize_skills",
    "materialize_working_skill",
    "render_working_skill",
    "target_dir",
    "working_source_hash",
]
