from __future__ import annotations

import sys
import types

from .skills import scan as _impl

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)


def __getattr__(name: str):
    return getattr(_impl, name)


class _FacadeModule(types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(_impl, name):
            setattr(_impl, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _FacadeModule


__all__ = [name for name in dir(_impl) if not name.startswith("_")]
