from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from support import chdir
from skillager.cli import main


class SkillagerRecommendTests(unittest.TestCase):

    def test_recommend_returns_body_safe_json_and_ranks_approved_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            dataframe = root / ".skills" / "dataframe"
            weather = root / ".skills" / "weather"
            dataframe.mkdir(parents=True)
            weather.mkdir(parents=True)
            (dataframe / "SKILL.md").write_text(
                "# DataFrame Help\n\nClean dataframe values.\n\nBody-only sentinel should stay out of recommend output.\n",
                encoding="utf-8",
            )
            (weather / "SKILL.md").write_text("# Weather Help\n\nUse weather guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--agent", "codex", "--no-bootstrap", "--no-packages", "--summary-json"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["recommend", "--goal", "clean dataframe values", "--agent", "codex", "--json"]), 0)

            payload = json.loads(output.getvalue())
            self.assertEqual(payload["schema"], "skillager.recommend.v1")
            self.assertEqual(payload["agent"], "codex")
            self.assertEqual(len(payload["candidates"]), 1)
            self.assertEqual(payload["candidates"][0]["skill_id"], "project/dataframe")
            self.assertIn(payload["candidates"][0]["recommended_exposure"], {"native", "stub"})
            self.assertNotIn("Body-only sentinel", output.getvalue())

    def test_recommend_reports_existing_router_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "mapping"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Mapping\n\nUse GIS mapping guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--agent", "codex", "--no-bootstrap", "--no-packages", "--summary-json"]), 0)
                    self.assertEqual(main(["tag", "create", "gis"]), 0)
                    self.assertEqual(main(["tag", "add", "gis", "project/mapping"]), 0)
                    self.assertEqual(main(["project", "attach-tag", "gis"]), 0)
                    self.assertEqual(main(["materialize", "--tag", "gis", "--mode", "router", "--agent", "codex", "--scope", "project"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["recommend", "--goal", "GIS mapping", "--agent", "codex", "--json"]), 0)

            payload = json.loads(output.getvalue())
            candidate = payload["candidates"][0]
            self.assertEqual(candidate["skill_id"], "project/mapping")
            self.assertEqual(candidate["current_exposure"], "router")
            self.assertEqual(candidate["recommended_exposure"], "none")
            self.assertTrue(candidate["materialized_targets"])

    def test_recommend_suggests_router_for_attached_tag_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            mapping = root / ".skills" / "mapping"
            geocode = root / ".skills" / "geocode"
            mapping.mkdir(parents=True)
            geocode.mkdir(parents=True)
            (mapping / "SKILL.md").write_text("# Mapping\n\nUse GIS mapping workflow guidance.\n", encoding="utf-8")
            (geocode / "SKILL.md").write_text("# Geocode\n\nUse GIS geocoding workflow guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--agent", "codex", "--no-bootstrap", "--no-packages", "--summary-json"]), 0)
                    self.assertEqual(main(["tag", "create", "gis"]), 0)
                    self.assertEqual(main(["tag", "add", "gis", "project/mapping"]), 0)
                    self.assertEqual(main(["tag", "add", "gis", "project/geocode"]), 0)
                    self.assertEqual(main(["project", "attach-tag", "gis"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["recommend", "--goal", "GIS workflow", "--agent", "codex", "--json"]), 0)

            payload = json.loads(output.getvalue())
            candidate = next(item for item in payload["candidates"] if item.get("kind") == "tag" and item.get("id") == "gis")
            self.assertEqual(candidate["recommended_exposure"], "router")
            self.assertEqual(candidate["member_count"], 2)
            self.assertEqual(set(candidate["member_ids"]), {"project/mapping", "project/geocode"})
            self.assertEqual(candidate["commands"]["router"], "skillager materialize --tag gis --mode router --agent codex --scope project")

    def test_recommend_collapses_same_content_duplicate_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            project_skill = root / ".skills" / "mapping"
            package_skill = root / ".venv" / "lib" / "python3.13" / "site-packages" / "demo_pkg" / ".skills" / "mapping"
            project_skill.mkdir(parents=True)
            package_skill.mkdir(parents=True)
            body = "# Mapping\n\nUse GIS mapping guidance.\n"
            (project_skill / "SKILL.md").write_text(body, encoding="utf-8")
            (package_skill / "SKILL.md").write_text(body, encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--agent", "codex", "--no-bootstrap", "--no-packages", "--summary-json"]), 0)
                    self.assertEqual(main(["index"]), 0)
                    self.assertEqual(main(["review", "demo-pkg/mapping", "--trust-selected", "reviewed"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["recommend", "--goal", "GIS mapping", "--agent", "codex", "--json"]), 0)

            payload = json.loads(output.getvalue())
            mapping_candidates = [
                candidate
                for candidate in payload["candidates"]
                if candidate.get("skill_id") in {"project/mapping", "demo-pkg/mapping"}
                or candidate.get("representative_id") in {"project/mapping", "demo-pkg/mapping"}
            ]
            self.assertEqual(len(mapping_candidates), 1)
            candidate = mapping_candidates[0]
            self.assertEqual(candidate["kind"], "group")
            self.assertEqual(set(candidate["duplicate_content"]["ids"]), {"project/mapping", "demo-pkg/mapping"})

    def test_recommend_empty_results_do_not_force_filler_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "dataframe"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# DataFrame\n\nUse dataframe cleanup guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--agent", "codex", "--no-bootstrap", "--no-packages", "--summary-json"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["recommend", "--goal", "zzzxqwv unmatched phrase", "--agent", "codex", "--json"]), 0)

            payload = json.loads(output.getvalue())
            self.assertEqual(payload["candidates"], [])
            self.assertIsNone(payload["commands"]["router"])
            self.assertIsNone(payload["commands"]["stub"])
            self.assertIsNone(payload["commands"]["native"])

    def test_recommend_limit_caps_candidate_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            for slug in ("analysis-one", "analysis-two", "analysis-three"):
                skill_dir = root / ".skills" / slug
                skill_dir.mkdir(parents=True)
                (skill_dir / "SKILL.md").write_text(f"# {slug.title()}\n\nUse analysis workflow guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--agent", "codex", "--no-bootstrap", "--no-packages", "--summary-json"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["recommend", "--goal", "analysis workflow", "--agent", "codex", "--limit", "2", "--json"]), 0)

            payload = json.loads(output.getvalue())
            self.assertEqual(len(payload["candidates"]), 2)

    def test_recommend_include_global_adds_global_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            home = Path(tmp) / "home"
            root.mkdir()
            state = Path(tmp) / ".skillager"
            global_skill = home / ".codex" / "skills" / "global-help"
            global_skill.mkdir(parents=True)
            (global_skill / "SKILL.md").write_text("# Global Help\n\nUse global-only guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=home),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--include-global", "--accept-low", "--agent", "codex", "--no-bootstrap", "--no-packages", "--summary-json"]), 0)
                default_output = StringIO()
                with redirect_stdout(default_output):
                    self.assertEqual(main(["recommend", "--goal", "global-only", "--agent", "codex", "--json"]), 0)
                include_output = StringIO()
                with redirect_stdout(include_output):
                    self.assertEqual(main(["recommend", "--goal", "global-only", "--agent", "codex", "--include-global", "--json"]), 0)

            self.assertEqual(json.loads(default_output.getvalue())["candidates"], [])
            include_payload = json.loads(include_output.getvalue())
            self.assertEqual(include_payload["candidates"][0]["skill_id"], "global/global-help")

    def test_recommend_includes_compatibility_problem_without_materialize_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / ".skills" / "claude-only"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Claude Only\n\nUse Claude-only guidance.\n", encoding="utf-8")
            (skill_dir / "skillager.yaml").write_text(
                "\n".join(
                    [
                        "schema: skillager.skill.v1",
                        "audience:",
                        "  - user",
                        "activation:",
                        "  default: manual",
                        "compatibility:",
                        "  incompatible_with:",
                        "    - codex",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["setup", "--source", "project", "--accept-low", "--agent", "codex", "--no-bootstrap", "--no-packages", "--summary-json"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["recommend", "--goal", "Claude-only guidance", "--agent", "codex", "--json"]), 0)
                compatible_output = StringIO()
                with redirect_stdout(compatible_output):
                    self.assertEqual(main(["recommend", "--goal", "Claude-only guidance", "--agent", "codex", "--compatible-only", "--json"]), 0)

            payload = json.loads(output.getvalue())
            candidate = payload["candidates"][0]
            self.assertEqual(candidate["compatibility"]["problem"], "incompatible with codex")
            self.assertEqual(candidate["recommended_exposure"], "none")
            self.assertIsNone(candidate["commands"]["stub"])
            self.assertIsNone(candidate["commands"]["native"])
            self.assertEqual(json.loads(compatible_output.getvalue())["candidates"], [])


if __name__ == "__main__":
    unittest.main()
