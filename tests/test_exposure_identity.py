from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from skillager.commands import impl as commands
from skillager.exposure import identity
from skillager.exposure.target_state import write_materialized_sidecar
from skillager.exposure.drift import _source_change
from skillager.exposure.impl import content_hashes
from skillager.exposure.management import _exposure_record


LIBRARY_A = "11111111-1111-4111-8111-111111111111"
LIBRARY_B = "22222222-2222-4222-8222-222222222222"


class ExposureIdentityTests(unittest.TestCase):
    def data(self, ids, members):
        return {"schema": "skillager.router.v1", "source_type": "skillager-router", "source_id": "skillager/example",
                "skill_ids": ids, "member_sources": members}

    def test_member_qualification_is_complete_unique_ordered_and_never_partially_trusts_malformed_metadata(self):
        valid = [{"skill_id": skill_id, "source_library_id": LIBRARY_A} for skill_id in ("lib/a", "lib/b")]
        unknown = [{**item, "source_library_id": None} for item in valid]
        data = self.data(["lib/a", "lib/b"], list(reversed(valid)))
        self.assertEqual(identity.router_member_sources(data), valid)
        for malformed in (None, {}, [], valid[:1], [valid[0], valid[0]], [valid[0], {**valid[1], "skill_id": "lib/other"}],
                          [valid[0], {**valid[1], "source_library_id": "not-a-uuid"}],
                          [valid[0], {**valid[1], "unexpected": True}], [valid[0], {"skill_id": "lib/b"}]):
            with self.subTest(malformed=malformed):
                data = self.data(["lib/a", "lib/b"], malformed)
                self.assertEqual(identity.router_member_sources(data), unknown)
                self.assertEqual(identity.recorded_source_keys(data), {"lib/a": None, "lib/b": None})
                with tempfile.TemporaryDirectory() as tmp:
                    record = _exposure_record(Path(tmp) / "router/skillager.materialized.yaml", data,
                                               fallback_agent="codex", fallback_scope="project")
                    self.assertEqual(record["member_sources"], unknown)
        self.assertEqual(identity.router_member_sources(self.data(["lib/a", "lib/a"], valid)), [])
        self.assertEqual(identity.router_member_sources(self.data(["lib/a", "lib/b"], [valid[0], unknown[1]])), [valid[0], unknown[1]])

    def test_large_router_aggregate_validation_parses_members_once_and_preserves_legacy_nonlibrary_policy(self):
        class RecordedIds(list):
            def __contains__(self, value):
                raise AssertionError("Router membership must use a set, not a per-member list scan")

        count = 5000
        started = time.monotonic()
        for prefix, recorded_library in (("lib", LIBRARY_A), ("legacy", None)):
            ids = RecordedIds(f"{prefix}/skill-{number}" for number in range(count))
            skills = [{"id": value, "content_hash": "a" * 64, "source": {"library_id": recorded_library}} for value in ids]
            data = self.data(ids, identity.member_sources(skills))
            data["source_hash"] = content_hashes(skills)
            current = {identity.skill_source_key(skill): skill["content_hash"] for skill in skills}
            with patch.object(identity, "router_member_sources", wraps=identity.router_member_sources) as parse:
                self.assertIsNone(_source_change(data, current))
                self.assertEqual(parse.call_count, 1)
            public = _exposure_record(Path("/fixture/router/skillager.materialized.yaml"), data,
                                       fallback_agent="claude", fallback_scope="project")
            self.assertEqual(len(public["member_sources"]), count)
            if prefix == "lib":
                foreign = {identity.source_key(skill_id, LIBRARY_B): "a" * 64 for skill_id in ids}
                change = _source_change(data, foreign)
                self.assertEqual(change["_status"], "source_unavailable")
                self.assertEqual(change["unavailable_skill_ids"], ids)
                data.pop("member_sources")
                self.assertEqual(_source_change(data, current)["_status"], "source_unavailable")
            else:
                data.pop("member_sources")
                self.assertIsNone(_source_change(data, current))
        print(f"5,000-member canonical/legacy identity projection: {time.monotonic() - started:.3f}s")

    def test_large_inventory_projects_router_once_without_repeating_member_arrays_in_public_rows(self):
        started = time.monotonic()
        count = 5000
        skills = [{"id": f"lib/skill-{number}", "content_hash": "a" * 64, "trust": "reviewed",
                   "source": {"library_id": LIBRARY_A, "ownership": "library"}} for number in range(count)]
        data = self.data([skill["id"] for skill in skills], identity.member_sources(skills))
        data["source_hash"] = content_hashes(skills)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            target = project / ".agents/skills/skillager-example"
            target.mkdir(parents=True)
            write_materialized_sidecar(target / "skillager.materialized.yaml", data)
            exposure = commands._project_exposure(project)
            inventory = [commands._with_project_inventory_fields(skill, exposure) for skill in skills]
            with patch.object(commands, "_exposure_source_is_current", wraps=commands._exposure_source_is_current) as classify:
                current = commands._filter_current_inventory_exposures(inventory)
                self.assertEqual(classify.call_count, 1)
            public = [commands._compact_skill_metadata(skill) for skill in current]
            for item in public:
                self.assertEqual(item["exposure"], "router")
                self.assertEqual(len(item["exposed_via"]), 1)
                self.assertEqual(item["exposed_via"][0]["source_library_id"], LIBRARY_A)
                self.assertNotIn("member_sources", item["exposed_via"][0])
            encoded = json.dumps(public)
            self.assertLess(len(encoded), count * 1500)
            full = commands._public_full_skill_metadata(current[0])
            self.assertNotIn("_member_sources", full["exposure_targets"][0])
        print(f"5,000-row public inventory projection: {len(encoded)} bytes in {time.monotonic() - started:.3f}s")
