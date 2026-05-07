from __future__ import annotations

import sys
import types

from .catalog import impl as _impl

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)

ack_collection_migrations = _impl.ack_collection_migrations
add_collection = _impl.add_collection
add_tag_skill = _impl.add_tag_skill
apply_collection_trust_migrations = _impl.apply_collection_trust_migrations
attach_project_tag = _impl.attach_project_tag
clear_project_tags = _impl.clear_project_tags
collection_migration_summary = _impl.collection_migration_summary
create_tag = _impl.create_tag
detach_project_tag = _impl.detach_project_tag
load_collections = _impl.load_collections
load_project_tags = _impl.load_project_tags
load_tags = _impl.load_tags
normalize_tag = _impl.normalize_tag
refresh_collection = _impl.refresh_collection
remove_collection = _impl.remove_collection
remove_tag_skill = _impl.remove_tag_skill
search_collection = _impl.search_collection
select_attached_tag_skills = _impl.select_attached_tag_skills
select_collection_skills = _impl.select_collection_skills
select_tag_skills = _impl.select_tag_skills
set_tag_skills = _impl.set_tag_skills


def __getattr__(name: str):
    return getattr(_impl, name)


class _FacadeModule(types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(_impl, name):
            setattr(_impl, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _FacadeModule


__all__ = [
    "ack_collection_migrations",
    "add_collection",
    "add_tag_skill",
    "apply_collection_trust_migrations",
    "attach_project_tag",
    "clear_project_tags",
    "collection_migration_summary",
    "create_tag",
    "detach_project_tag",
    "load_collections",
    "load_project_tags",
    "load_tags",
    "normalize_tag",
    "refresh_collection",
    "remove_collection",
    "remove_tag_skill",
    "search_collection",
    "select_attached_tag_skills",
    "select_collection_skills",
    "select_tag_skills",
    "set_tag_skills",
]
