from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from support import chdir
from skillager.cli import main
from skillager.index import build_index
from skillager.materialize import explicit_router_slug
from skillager.paths import project_state_root
from skillager.simple_yaml import load_mapping
from skillager.trust import content_hash, set_trust, trust_state


class SkillagerCollectionsTagsTests(unittest.TestCase):

    def test_collection_review_feeds_global_inventory_and_project_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            skill_dir = collection / "gis-domain"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# GIS Domain\n\nUse GIS domain concepts.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["collection", "search", "community", "gis"]), 0)
                self.assertIn("community/gis-domain", output.getvalue())

                unattached = StringIO()
                with redirect_stdout(unattached):
                    self.assertEqual(main(["working", "--json"]), 0)
                unattached_data = json.loads(unattached.getvalue())
                self.assertFalse(unattached_data["can_proceed"])
                self.assertEqual(unattached_data["pending_owner_review_count"], 1)
                self.assertEqual(unattached_data["pending_external_review_count"], 1)

                collection_list = StringIO()
                with redirect_stdout(collection_list):
                    self.assertEqual(main(["collection", "list", "--json"]), 0)
                collection_list_data = json.loads(collection_list.getvalue())
                self.assertEqual(sorted(collection_list_data["collections"]), ["community"])
                project_tags = StringIO()
                with redirect_stdout(project_tags):
                    self.assertEqual(main(["tag", "list", "--json"]), 0)
                self.assertEqual(json.loads(project_tags.getvalue())["attached_tags"], [])

                setup = StringIO()
                with redirect_stdout(setup):
                    self.assertEqual(main(["setup", "--no-packages", "--json"]), 0)
                setup_data = json.loads(setup.getvalue())
                self.assertEqual([skill["id"] for skill in setup_data["selected"]], ["community/gis-domain"])

                review = StringIO()
                with redirect_stdout(review):
                    self.assertEqual(main(["setup", "--no-packages", "--accept-low", "--json"]), 0)
                review_data = json.loads(review.getvalue())
                self.assertEqual(review_data["summary"]["by_trust"], {"reviewed": 1})

                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "enable", "community", "--tag", "gis"]), 0)

                raw_collection_review = StringIO()
                with redirect_stdout(raw_collection_review):
                    self.assertEqual(main(["review", "--source", "collection", "--json"]), 0)
                raw_collection_data = json.loads(raw_collection_review.getvalue())
                self.assertEqual([skill["id"] for skill in raw_collection_data["selected"]], ["community/gis-domain"])

                compact_setup = StringIO()
                with redirect_stdout(compact_setup):
                    self.assertEqual(main(["setup", "--no-packages", "--summary-json"]), 0)
                compact_data = json.loads(compact_setup.getvalue())
                self.assertEqual(compact_data["selected"], 1)
                self.assertEqual(compact_data["selected_ids"], ["community/gis-domain"])
                self.assertNotIn("selected", compact_data.get("action", {}))

                working = StringIO()
                with redirect_stdout(working):
                    self.assertEqual(main(["working", "--json"]), 0)
                working_data = json.loads(working.getvalue())
                self.assertTrue(working_data["readiness"]["review_ready"])
                self.assertEqual(working_data["pending_owner_review_count"], 0)

                project_tags = StringIO()
                with redirect_stdout(project_tags):
                    self.assertEqual(main(["tag", "list", "--json"]), 0)
                project_tag_data = json.loads(project_tags.getvalue())
                self.assertEqual(project_tag_data["attached_tags"], ["gis"])
                self.assertEqual(project_tag_data["tag_summaries"][0]["available"], 1)

                inventory_output = StringIO()
                with redirect_stdout(inventory_output):
                    self.assertEqual(main(["list", "--summary-json", "--agent", "codex"]), 0)
                inventory_data = json.loads(inventory_output.getvalue())
                self.assertEqual(inventory_data["total"], 1)
                self.assertEqual(inventory_data["counts"]["by_source"], {"collection": 1})
                self.assertNotIn("by_risk", inventory_data["counts"])
                self.assertNotIn("risk", inventory_data["skills"][0])

                scan_output = StringIO()
                with redirect_stdout(scan_output):
                    self.assertEqual(main(["collection", "show", "community/gis-domain", "--json"]), 0)
                scan_data = json.loads(scan_output.getvalue())
                self.assertEqual(scan_data["scan"]["risk"], "low")
                self.assertEqual(scan_data["lint"]["status"], "ok")
                self.assertNotIn("# GIS Domain", scan_output.getvalue())

                collection_text = StringIO()
                with redirect_stdout(collection_text):
                    self.assertEqual(main(["collection", "list"]), 0)
                self.assertIn("community", collection_text.getvalue())
                self.assertNotIn("collection enable", collection_text.getvalue())

                search_output = StringIO()
                with redirect_stdout(search_output):
                    self.assertEqual(main(["search", "gis", "--json"]), 0)
                search_data = json.loads(search_output.getvalue())
                self.assertEqual([skill["id"] for skill in search_data], ["community/gis-domain"])
                self.assertIn("gis", search_data[0]["tags"])
                self.assertTrue(search_data[0]["available"])
                self.assertNotIn("trust", search_data[0])
                self.assertNotIn("risk", search_data[0])

                show_output = StringIO()
                with redirect_stdout(show_output):
                    self.assertEqual(main(["show", "community/gis-domain", "--json"]), 0)
                show_data = json.loads(show_output.getvalue())
                self.assertTrue(show_data["skill"]["available"])
                self.assertNotIn("trust", show_data["skill"])
                self.assertIn("attached-tag", show_data["skill"]["availability"])
                self.assertNotIn("scan", show_data["skill"])
                self.assertNotIn("lint", show_data["skill"])

                collection_show = StringIO()
                with redirect_stdout(collection_show):
                    self.assertEqual(main(["collection", "show", "community/gis-domain", "--json"]), 0)
                collection_data = json.loads(collection_show.getvalue())
                self.assertNotIn("availability", collection_data)
                self.assertNotIn("exposure", collection_data)

    def test_collection_lint_blocked_entries_are_diagnostic_only_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            good = collection / "gis-domain"
            bad = collection / "bad"
            good.mkdir(parents=True)
            bad.mkdir(parents=True)
            (good / "SKILL.md").write_text("# GIS Domain\n\nUse GIS domain concepts.\n", encoding="utf-8")
            (bad / "SKILL.md").write_text("# Bad\n\nUse ordinary bad fixture guidance.\n", encoding="utf-8")
            (bad / "skillager.yaml").write_text(
                "schema: skillager.skill.v1\nsummary: hostile collection bait\naudience:\n  - user\nactivation:\n  default: manual\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)

                default_search = StringIO()
                with redirect_stdout(default_search):
                    self.assertEqual(main(["collection", "search", "community", "community/bad", "--json"]), 0)
                self.assertEqual(json.loads(default_search.getvalue()), [])

                hidden_show = StringIO()
                with redirect_stderr(hidden_show):
                    self.assertEqual(main(["collection", "show", "community/bad", "--json"]), 2)

                diagnostic_show = StringIO()
                with redirect_stdout(diagnostic_show):
                    self.assertEqual(main(["collection", "show", "community/bad", "--include-lint-blocked", "--json"]), 0)
                diagnostic = json.loads(diagnostic_show.getvalue())
                self.assertEqual(diagnostic["trust"], "lint_blocked")
                self.assertNotIn("hostile collection bait", diagnostic_show.getvalue())

                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "enable", "community", "--tag", "all"]), 0)
                tag_output = StringIO()
                with redirect_stdout(tag_output):
                    self.assertEqual(main(["tag", "show", "all", "--json"]), 0)
                tag_data = json.loads(tag_output.getvalue())
                self.assertEqual([skill["id"] for skill in tag_data["skills"]], [])

    def test_tag_add_from_collection_and_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            first = collection / "first"
            second = collection / "second"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (first / "SKILL.md").write_text("# First\n\nUse first guidance.\n", encoding="utf-8")
            (second / "SKILL.md").write_text("# Second\n\nUse second guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["setup", "--source", "collection", "--accept-low", "--json"]), 0)
                    self.assertEqual(main(["tag", "add", "all-community", "--from-collection", "community"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["tag", "show", "all-community", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual([skill["id"] for skill in data["skills"]], ["community/first", "community/second"])
            tags = json.loads((state / "tags.json").read_text(encoding="utf-8"))
            self.assertEqual(set(tags["tags"]["all-community"]["skills"]), {"community/first", "community/second"})

    def test_tag_add_from_collection_sync_replaces_existing_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            first = collection / "first"
            first.mkdir(parents=True)
            (first / "SKILL.md").write_text("# First\n\nUse first guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["setup", "--source", "collection", "--accept-low", "--json"]), 0)
                    self.assertEqual(main(["tag", "create", "mixed"]), 0)
                    self.assertEqual(main(["tag", "add", "mixed", "community/first"]), 0)
                    self.assertEqual(main(["tag", "add", "mixed", "--from-collection", "community", "--sync"]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["tag", "show", "mixed", "--json"]), 0)
            data = json.loads(output.getvalue())
            self.assertEqual([skill["id"] for skill in data["skills"]], ["community/first"])
            tags = json.loads((state / "tags.json").read_text(encoding="utf-8"))
            self.assertEqual(tags["tags"]["mixed"]["source_collections"], ["community"])
            self.assertEqual(tags["tags"]["mixed"]["skills"], ["community/first"])

    def test_tag_and_project_tag_commands_surface_mixed_trust(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            reviewed = collection / "reviewed"
            unreviewed = collection / "unreviewed"
            reviewed.mkdir(parents=True)
            unreviewed.mkdir(parents=True)
            (reviewed / "SKILL.md").write_text("# Reviewed\n\nUse reviewed guidance.\n", encoding="utf-8")
            (unreviewed / "SKILL.md").write_text("# Unreviewed\n\nUse unreviewed guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    index = json.loads((state / "collections" / "community.json").read_text(encoding="utf-8"))
                    reviewed_skill = next(skill for skill in index["skills"] if skill["id"] == "community/reviewed")
                    set_trust(
                        state,
                        reviewed_skill["id"],
                        "reviewed",
                        reviewed_skill["content_hash"],
                        reviewed_skill["source"],
                        approval_key=reviewed_skill.get("approval_key"),
                    )
                    (state / "tags.json").write_text(
                        json.dumps(
                            {
                                "schema": "skillager.project-tags.v1",
                                "tags": {
                                    "mixed": {
                                        "skills": ["community/reviewed", "community/unreviewed"],
                                    }
                                },
                            },
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )

                tag_output = StringIO()
                with redirect_stdout(tag_output):
                    self.assertEqual(main(["tag", "show", "mixed", "--json"]), 0)
                attach_text = StringIO()
                with redirect_stdout(attach_text):
                    self.assertEqual(main(["tag", "show", "mixed"]), 0)
                project_output = StringIO()
                with redirect_stdout(project_output):
                    self.assertEqual(main(["tag", "list", "--json"]), 0)

            tag_data = json.loads(tag_output.getvalue())
            self.assertEqual(tag_data["summary"]["available"], 1)
            self.assertEqual(tag_data["summary"]["pending_owner_review"], 1)
            self.assertIn("need owner review", attach_text.getvalue())
            project_data = json.loads(project_output.getvalue())
            self.assertEqual(project_data["tag_summaries"][0]["tag"], "mixed")
            self.assertEqual(project_data["tag_summaries"][0]["pending_owner_review"], 1)

    def test_collection_add_recursively_indexes_repo_tree_with_path_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            repos = root / "repos"
            legacy = repos / "legacy-root"
            review = repos / "project-a" / ".skills" / "review-pr"
            deploy = repos / "project-b" / "skills" / "deploy-preview"
            materialized = repos / "project-c" / ".agents" / "skills" / "old"
            legacy.mkdir(parents=True)
            review.mkdir(parents=True)
            deploy.mkdir(parents=True)
            materialized.mkdir(parents=True)
            (legacy / "SKILL.md").write_text("# Legacy\n\nUse legacy guidance.\n", encoding="utf-8")
            (legacy / "skillager.yaml").write_text(
                "\n".join(
                    [
                        "schema: skillager.skill.v1",
                        "audience:",
                        "  - user",
                        "activation:",
                        "  default: manual",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (review / "SKILL.md").write_text("# Review PR\n\nReview pull requests.\n", encoding="utf-8")
            (deploy / "SKILL.md").write_text("# Deploy Preview\n\nDeploy previews.\n", encoding="utf-8")
            (materialized / "SKILL.md").write_text("# Old\n\nOld materialized skill.\n", encoding="utf-8")
            (materialized / "skillager.materialized.yaml").write_text("schema: skillager.materialized.v1\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["collection", "add", str(repos), "--name", "personal"]), 0)
                self.assertIn("personal: indexed 3 skill(s)", output.getvalue())
                index = json.loads((state / "collections" / "personal.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [skill["id"] for skill in index["skills"]],
                ["personal/legacy-root", "personal/project-a/review-pr", "personal/project-b/deploy-preview"],
            )

    def test_collection_add_ignores_nested_conda_envs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            skill_dir = collection / "gis-domain"
            top_level_conda_bait = collection / ".conda" / "skills" / "conda-bait"
            named_env_conda_bait = collection / ".conda" / "envs" / "gis" / "skills" / "named-env-bait"
            skill_dir.mkdir(parents=True)
            top_level_conda_bait.mkdir(parents=True)
            named_env_conda_bait.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# GIS Domain\n\nUse GIS domain concepts.\n", encoding="utf-8")
            (top_level_conda_bait / "SKILL.md").write_text("# Conda Bait\n\nUse top-level conda bait.\n", encoding="utf-8")
            (named_env_conda_bait / "SKILL.md").write_text("# Named Env Bait\n\nUse named conda env bait.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                index = json.loads((state / "collections" / "community.json").read_text(encoding="utf-8"))
            self.assertEqual([skill["id"] for skill in index["skills"]], ["community/gis-domain"])

    def test_collection_refresh_migrates_flattened_trust_and_tag_by_old_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "skills"
            skill_dir = collection / "python" / "foo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Foo\n\nUse foo guidance.\n", encoding="utf-8")
            digest = content_hash(skill_dir)
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "personal"]), 0)
                (state / "collections" / "personal.json").write_text(
                    json.dumps(
                        {
                            "schema": "skillager.collection-index.v1",
                            "name": "personal",
                            "path": str(collection),
                            "skills": [{"id": "personal/foo", "root": str(skill_dir), "content_hash": digest}],
                            "errors": [],
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                set_trust(state, "personal/foo", "reviewed", digest, {"type": "collection", "collection": "personal"})
                (state / "tags.json").write_text(json.dumps({"tags": {"python": ["personal/foo"]}}, indent=2) + "\n", encoding="utf-8")

                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "refresh", "personal"]), 0)

                new_index = json.loads((state / "collections" / "personal.json").read_text(encoding="utf-8"))
                self.assertEqual([skill["id"] for skill in new_index["skills"]], ["personal/python/foo"])
                new_hash = new_index["skills"][0]["content_hash"]
                self.assertEqual(trust_state(state, "personal/python/foo", new_hash), "reviewed")
                tags = json.loads((state / "tags.json").read_text(encoding="utf-8"))
                self.assertEqual(tags["tags"]["python"], ["personal/python/foo"])

                status = StringIO()
                with redirect_stdout(status):
                    self.assertEqual(main(["doctor", "--no-packages", "--json"]), 13)
                status_data = json.loads(status.getvalue())
                self.assertTrue(status_data["state"]["migration"]["pending"])
                self.assertEqual(status_data["state"]["migration"]["totals"]["trust_migrated"], 1)
                self.assertEqual(status_data["state"]["migration"]["totals"]["tag_migrated"], 1)

                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["doctor", "--no-packages", "--ack-migration", "--json"]), 10)
                acked = StringIO()
                with redirect_stdout(acked):
                    self.assertEqual(main(["doctor", "--no-packages", "--json"]), 10)
                self.assertFalse(json.loads(acked.getvalue())["state"]["migration"]["pending"])
                trust = json.loads((state / "trust.json").read_text(encoding="utf-8"))
                self.assertIn("personal/foo", trust["skills"])
                self.assertIn("personal/python/foo", trust["skills"])

    def test_collection_migration_ack_hash_changes_for_later_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            first = root / "first"
            second = root / "second"
            first_skill = first / "python" / "foo"
            second_skill = second / "writing" / "bar"
            first_skill.mkdir(parents=True)
            second_skill.mkdir(parents=True)
            (first_skill / "SKILL.md").write_text("# Foo\n\nUse foo.\n", encoding="utf-8")
            (second_skill / "SKILL.md").write_text("# Bar\n\nUse bar.\n", encoding="utf-8")
            first_hash = content_hash(first_skill)
            second_hash = content_hash(second_skill)
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(first), "--name", "first"]), 0)
                    self.assertEqual(main(["collection", "add", str(second), "--name", "second"]), 0)
                (state / "collections" / "first.json").write_text(
                    json.dumps(
                        {
                            "schema": "skillager.collection-index.v1",
                            "name": "first",
                            "path": str(first),
                            "skills": [{"id": "first/foo", "root": str(first_skill), "content_hash": first_hash}],
                            "errors": [],
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (state / "collections" / "second.json").write_text(
                    json.dumps(
                        {
                            "schema": "skillager.collection-index.v1",
                            "name": "second",
                            "path": str(second),
                            "skills": [{"id": "second/bar", "root": str(second_skill), "content_hash": second_hash}],
                            "errors": [],
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "refresh", "first"]), 0)
                first_status = StringIO()
                with redirect_stdout(first_status):
                    self.assertEqual(main(["doctor", "--no-packages", "--json"]), 13)
                first_data = json.loads(first_status.getvalue())
                self.assertTrue(first_data["state"]["migration"]["pending"])
                first_digest = first_data["state"]["migration"]["hash"]
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["doctor", "--no-packages", "--ack-migration", "--json"]), 10)
                    self.assertEqual(main(["collection", "refresh", "second"]), 0)
                second_status = StringIO()
                with redirect_stdout(second_status):
                    self.assertEqual(main(["doctor", "--no-packages", "--json"]), 13)
                second_data = json.loads(second_status.getvalue())
            self.assertTrue(second_data["state"]["migration"]["pending"])
            self.assertNotEqual(second_data["state"]["migration"]["hash"], first_digest)

    def test_collection_migration_preserves_project_local_trust_for_external_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog_state = root / "catalog-state"
            project = root / "project"
            project.mkdir()
            collection = root / "skills"
            skill_dir = collection / "python" / "foo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Foo\n\nUse foo guidance.\n", encoding="utf-8")
            digest = content_hash(skill_dir)
            with patch.dict(os.environ, {"NO_COLOR": "1"}, clear=True), patch("pathlib.Path.home", return_value=root), chdir(project):
                project_state = project_state_root(project)
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["--catalog-state-dir", str(catalog_state), "collection", "add", str(collection), "--name", "personal"]), 0)
                (catalog_state / "collections" / "personal.json").write_text(
                    json.dumps(
                        {
                            "schema": "skillager.collection-index.v1",
                            "name": "personal",
                            "path": str(collection),
                            "skills": [{"id": "personal/foo", "root": str(skill_dir), "content_hash": digest}],
                            "errors": [],
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (project / ".skillager").mkdir()
                (project / ".skillager" / "tags.json").write_text(
                    json.dumps(
                        {
                            "schema": "skillager.project-tags.v1",
                            "catalog_state_dir": str(catalog_state.resolve()),
                            "tags": {"python": {"skills": ["personal/foo"], "source_collections": ["personal"]}},
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                set_trust(project_state, "personal/foo", "reviewed", digest, {"type": "collection", "collection": "personal"})

                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["--catalog-state-dir", str(catalog_state), "collection", "refresh", "personal"]), 0)
                status = StringIO()
                with redirect_stdout(status):
                    self.assertEqual(main(["--catalog-state-dir", str(catalog_state), "working", "--json"]), 0)
                status_data = json.loads(status.getvalue())
                self.assertTrue(status_data["readiness"]["review_ready"])
                self.assertEqual(status_data["readiness"]["exposure"]["approved"], 1)
                new_hash = json.loads((catalog_state / "collections" / "personal.json").read_text(encoding="utf-8"))["skills"][0]["content_hash"]
            self.assertEqual(trust_state(project_state, "personal/python/foo", new_hash), "reviewed")

    def test_collection_inventory_uses_migrated_project_local_trust_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog_state = root / "catalog-state"
            project_a = root / "project-a"
            project_b = root / "project-b"
            project_a.mkdir()
            project_b.mkdir()
            collection = root / "skills"
            skill_dir = collection / "python" / "foo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Foo\n\nUse foo guidance.\n", encoding="utf-8")
            digest = content_hash(skill_dir)
            with patch.dict(os.environ, {"NO_COLOR": "1"}, clear=True), patch("pathlib.Path.home", return_value=root):
                with chdir(project_a), redirect_stdout(StringIO()):
                    self.assertEqual(main(["--catalog-state-dir", str(catalog_state), "collection", "add", str(collection), "--name", "personal"]), 0)
                (catalog_state / "collections" / "personal.json").write_text(
                    json.dumps(
                        {
                            "schema": "skillager.collection-index.v1",
                            "name": "personal",
                            "path": str(collection),
                            "skills": [{"id": "personal/foo", "root": str(skill_dir), "content_hash": digest}],
                            "errors": [],
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                project_a_state = project_state_root(project_a)
                set_trust(project_a_state, "personal/foo", "reviewed", digest, {"type": "collection", "collection": "personal"})

                with chdir(project_b), redirect_stdout(StringIO()):
                    self.assertEqual(main(["--catalog-state-dir", str(catalog_state), "collection", "refresh", "personal"]), 0)

                search = StringIO()
                with chdir(project_a), redirect_stdout(search):
                    self.assertEqual(main(["--catalog-state-dir", str(catalog_state), "search", "foo", "--json"]), 0)
                search_data = json.loads(search.getvalue())
                self.assertEqual([skill["id"] for skill in search_data], ["personal/python/foo"])
                self.assertTrue(search_data[0]["available"])

                trust = json.loads((project_a_state / "trust.json").read_text(encoding="utf-8"))
                self.assertIn("personal/foo", trust["skills"])
                self.assertNotIn("personal/python/foo", trust["skills"])

    def test_collection_refresh_reports_changed_content_as_needing_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "skills"
            skill_dir = collection / "python" / "foo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Foo\n\nUse foo guidance.\n", encoding="utf-8")
            old_digest = content_hash(skill_dir)
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "personal"]), 0)
                (state / "collections" / "personal.json").write_text(
                    json.dumps(
                        {
                            "schema": "skillager.collection-index.v1",
                            "name": "personal",
                            "path": str(collection),
                            "skills": [{"id": "personal/foo", "root": str(skill_dir), "content_hash": old_digest}],
                            "errors": [],
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                set_trust(state, "personal/foo", "reviewed", old_digest, {"type": "collection", "collection": "personal"})
                (skill_dir / "SKILL.md").write_text("# Foo\n\nUse changed foo guidance.\n", encoding="utf-8")
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "refresh", "personal"]), 0)

                new_index = json.loads((state / "collections" / "personal.json").read_text(encoding="utf-8"))
                new_hash = new_index["skills"][0]["content_hash"]
                self.assertEqual(trust_state(state, "personal/python/foo", new_hash), "discovered")
                migrations = json.loads((state / "collection_migrations.json").read_text(encoding="utf-8"))
                outcome = migrations["collections"]["personal"]
            self.assertEqual(outcome["needs_review"][0]["reason"], "content changed since last collection refresh")

    def test_collection_refresh_reports_ambiguous_flattened_trust_with_same_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "skills"
            python = collection / "python" / "foo"
            writing = collection / "writing" / "foo"
            python.mkdir(parents=True)
            writing.mkdir(parents=True)
            text = "# Foo\n\nUse shared foo.\n"
            (python / "SKILL.md").write_text(text, encoding="utf-8")
            (writing / "SKILL.md").write_text(text, encoding="utf-8")
            digest = content_hash(python)
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "personal"]), 0)
                (state / "collections" / "personal.json").write_text(
                    json.dumps(
                        {
                            "schema": "skillager.collection-index.v1",
                            "name": "personal",
                            "path": str(collection),
                            "skills": [
                                {"id": "personal/foo", "root": str(python), "content_hash": digest},
                                {"id": "personal/foo", "root": str(writing), "content_hash": digest},
                            ],
                            "errors": [],
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                set_trust(state, "personal/foo", "reviewed", digest, {"type": "collection", "collection": "personal"})
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "refresh", "personal"]), 0)
                migrations = json.loads((state / "collection_migrations.json").read_text(encoding="utf-8"))
                details = StringIO()
                with redirect_stdout(details):
                    self.assertEqual(main(["doctor", "--no-packages", "--migration-details"]), 13)
            self.assertEqual(migrations["collections"]["personal"]["needs_review"][0]["reason"], "ambiguous old ID/content hash")
            self.assertIn("needs review: personal/foo (ambiguous old ID/content hash)", details.getvalue())

    def test_collection_refresh_reports_ambiguous_flattened_tag_membership(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "skills"
            python = collection / "python" / "foo"
            writing = collection / "writing" / "foo"
            python.mkdir(parents=True)
            writing.mkdir(parents=True)
            (python / "SKILL.md").write_text("# Python Foo\n\nUse python foo.\n", encoding="utf-8")
            (writing / "SKILL.md").write_text("# Writing Foo\n\nUse writing foo.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "personal"]), 0)
                (state / "collections" / "personal.json").write_text(
                    json.dumps(
                        {
                            "schema": "skillager.collection-index.v1",
                            "name": "personal",
                            "path": str(collection),
                            "skills": [
                                {"id": "personal/foo", "root": str(python), "content_hash": content_hash(python)},
                                {"id": "personal/foo", "root": str(writing), "content_hash": content_hash(writing)},
                            ],
                            "errors": [],
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (state / "tags.json").write_text(json.dumps({"tags": {"foo": ["personal/foo"]}}, indent=2) + "\n", encoding="utf-8")
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "refresh", "personal"]), 0)
                tags = json.loads((state / "tags.json").read_text(encoding="utf-8"))
                migrations = json.loads((state / "collection_migrations.json").read_text(encoding="utf-8"))
            self.assertEqual(tags["tags"]["foo"], ["personal/foo"])
            self.assertEqual(
                migrations["collections"]["personal"]["tag_needs_repair"][0]["candidate_ids"],
                ["personal/python/foo", "personal/writing/foo"],
            )

    def test_catalog_collections_and_review_are_global_but_tags_are_project_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog_state = root / "catalog-state"
            project_a = root / "project-a"
            project_b = root / "project-b"
            project_a.mkdir()
            project_b.mkdir()
            (project_a / "pyproject.toml").write_text("[project]\nname = \"a\"\n", encoding="utf-8")
            (project_b / "pyproject.toml").write_text("[project]\nname = \"b\"\n", encoding="utf-8")
            collection = root / "community"
            skill_dir = collection / "gis-domain"
            topo_dir = collection / "topology"
            skill_dir.mkdir(parents=True)
            topo_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# GIS Domain\n\nUse GIS domain concepts.\n", encoding="utf-8")
            (topo_dir / "SKILL.md").write_text("# Topology\n\nUse topology concepts.\n", encoding="utf-8")

            with patch.dict(os.environ, {"SKILLAGER_CATALOG_STATE_DIR": str(catalog_state), "NO_COLOR": "1"}):
                os.environ.pop("SKILLAGER_STATE_DIR", None)
                with patch("pathlib.Path.home", return_value=root):
                    with chdir(project_a), redirect_stdout(StringIO()):
                        self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                        self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--accept-low", "--json"]), 0)
                        self.assertEqual(main(["tag", "create", "gis"]), 0)
                        self.assertEqual(main(["tag", "add", "gis", "community/gis-domain", "community/topology"]), 0)
                        self.assertEqual(main(["tag", "remove", "gis", "community/topology"]), 0)

                    self.assertTrue((catalog_state / "collections.json").exists())
                    self.assertFalse((project_a / ".skillager" / "collections.json").exists())

                    unattached = StringIO()
                    with chdir(project_b), redirect_stdout(unattached):
                        self.assertEqual(main(["list", "--summary-json", "--agent", "codex"]), 0)
                    unattached_data = json.loads(unattached.getvalue())
                    self.assertEqual(unattached_data["total"], 2)
                    project_b_working = StringIO()
                    with chdir(project_b), redirect_stdout(project_b_working):
                        self.assertEqual(main(["working", "--json"]), 0)
                    self.assertTrue(json.loads(project_b_working.getvalue())["readiness"]["review_ready"])
                    project_b_tags = StringIO()
                    with chdir(project_b), redirect_stdout(project_b_tags):
                        self.assertEqual(main(["tag", "list", "--json"]), 0)
                    self.assertEqual(json.loads(project_b_tags.getvalue())["attached_tags"], [])

                    project_b_search = StringIO()
                    with chdir(project_b), redirect_stdout(project_b_search):
                        self.assertEqual(main(["search", "gis", "--json"]), 0)
                    self.assertEqual([skill["id"] for skill in json.loads(project_b_search.getvalue())], ["community/gis-domain"])

                    with chdir(project_b), redirect_stdout(StringIO()):
                        self.assertEqual(main(["tag", "add", "gis", "community/gis-domain"]), 0)
                    self.assertTrue((project_b / ".skillager" / "tags.json").exists())
                    self.assertTrue((project_a / ".skillager" / "tags.json").exists())

                    review = StringIO()
                    with chdir(project_b), redirect_stdout(review):
                        self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--accept-low", "--json"]), 0)
                    review_data = json.loads(review.getvalue())
                    self.assertEqual(review_data["summary"]["by_trust"], {"reviewed": 2})

                    raw_review = StringIO()
                    with chdir(project_a), redirect_stdout(raw_review):
                        self.assertEqual(main(["review", "--source", "collection", "--json"]), 0)
                    raw_review_data = json.loads(raw_review.getvalue())
                    self.assertEqual({skill["id"] for skill in raw_review_data["selected"]}, {"community/gis-domain", "community/topology"})

                    project_b_status = StringIO()
                    with chdir(project_b), redirect_stdout(project_b_status):
                        self.assertEqual(main(["tag", "list", "--json"]), 0)
                    project_b_status_data = json.loads(project_b_status.getvalue())
                    self.assertEqual(project_b_status_data["attached_tags"], ["gis"])
                    self.assertEqual(project_b_status_data["tag_summaries"][0]["available"], 1)

                    project_a_status = StringIO()
                    with chdir(project_a), redirect_stdout(project_a_status):
                        self.assertEqual(main(["list", "--summary-json", "--agent", "codex"]), 0)
                    self.assertEqual(json.loads(project_a_status.getvalue())["total"], 2)

    def test_project_tag_remembers_external_catalog_for_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog_state = root / "catalog-state"
            project = root / "project"
            project.mkdir()
            collection = root / "community"
            skill_dir = collection / "gis-domain"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# GIS Domain\n\nUse GIS domain concepts.\n", encoding="utf-8")

            with patch.dict(os.environ, {"NO_COLOR": "1"}, clear=True), patch("pathlib.Path.home", return_value=root), chdir(project):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["--catalog-state-dir", str(catalog_state), "collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["--catalog-state-dir", str(catalog_state), "setup", "--source", "collection", "--accept-low", "--json"]), 0)
                    self.assertEqual(main(["--catalog-state-dir", str(catalog_state), "tag", "create", "gis"]), 0)
                    self.assertEqual(main(["--catalog-state-dir", str(catalog_state), "tag", "add", "gis", "community/gis-domain"]), 0)

                project_tags = json.loads((project / ".skillager" / "tags.json").read_text(encoding="utf-8"))
                self.assertEqual(project_tags["catalog_state_dir"], str(catalog_state.resolve()))

                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["expose", "--tag", "gis", "--mode", "router", "--agent", "codex"]), 0)
                activated = StringIO()
                with redirect_stdout(activated):
                    self.assertEqual(main(["activate", "community/gis-domain", "--from-router", "skillager-gis"]), 0)
                self.assertIn("# GIS Domain", activated.getvalue())
                self.assertIn(f"Source root: `{collection.resolve()}`", activated.getvalue())
                self.assertIn("Resolve relative paths and run repository-local commands from the source root", activated.getvalue())

    def test_tag_router_exposure_and_guarded_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            gis = collection / "gis-domain"
            other = collection / "other"
            gis.mkdir(parents=True)
            other.mkdir(parents=True)
            (gis / "SKILL.md").write_text("# GIS Domain\n\nUse GIS domain concepts.\n", encoding="utf-8")
            (other / "SKILL.md").write_text("# Other\n\nUse unrelated concepts.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--accept-low"]), 0)
                    self.assertEqual(main(["tag", "create", "gis"]), 0)
                    self.assertEqual(main(["tag", "add", "gis", "community/gis-domain"]), 0)
                (state / "status_scope.json").write_text(
                    json.dumps({"schema": "skillager.status-scope.v1", "selected_count": 49, "baseline": {}}),
                    encoding="utf-8",
                )

                router_output = StringIO()
                with redirect_stdout(router_output):
                    self.assertEqual(main(["expose", "--tag", "gis", "--mode", "router", "--agent", "codex"]), 0)
                self.assertIn("Continue curation:", router_output.getvalue())
                self.assertIn("skillager working --agent codex", router_output.getvalue())
                self.assertNotIn("handoff", router_output.getvalue())
                saved_scope = json.loads((state / "status_scope.json").read_text(encoding="utf-8"))
                self.assertEqual(saved_scope["selected_count"], 49)
                self.assertFalse((root / ".agents" / "skills" / "skillager-working" / "SKILL.md").exists())
                self.assertFalse((root / "AGENTS.md").exists())
                router = root / ".agents" / "skills" / "skillager-gis" / "SKILL.md"
                router_text = router.read_text(encoding="utf-8")
                self.assertIn("community/gis-domain", router_text)
                self.assertIn("Use GIS domain concepts.", router_text)
                self.assertIn("skillager activate <skill-id> --from-router skillager-gis", router_text)

                search_output = StringIO()
                with redirect_stdout(search_output):
                    self.assertEqual(main(["search", "gis", "--tag", "gis", "--json"]), 0)
                search_data = json.loads(search_output.getvalue())
                self.assertEqual([item["id"] for item in search_data], ["community/gis-domain"])
                self.assertEqual(search_data[0]["exposure"], "router")
                self.assertNotIn("materialized_targets", search_data[0])
                self.assertEqual(search_data[0]["exposed_via"][0]["kind"], "router")
                self.assertEqual(search_data[0]["exposed_via"][0]["router_slug"], "skillager-gis")
                normal_search = StringIO()
                with redirect_stdout(normal_search):
                    self.assertEqual(main(["search", "gis", "--json"]), 0)
                normal_search_data = json.loads(normal_search.getvalue())
                self.assertEqual(normal_search_data[0]["exposure"], "router")
                self.assertNotIn("materialized_targets", normal_search_data[0])
                self.assertEqual(normal_search_data[0]["exposed_via"][0]["kind"], "router")
                self.assertNotIn("scan", normal_search_data[0])

                working_output = StringIO()
                with redirect_stdout(working_output):
                    self.assertEqual(main(["working", "--agent", "codex", "--json"]), 0)
                working_data = json.loads(working_output.getvalue())
                self.assertEqual(working_data["readiness"]["exposure"]["exposed"], 1)
                self.assertEqual(working_data["readiness"]["exposure"]["router_tags"], 1)

                activate_output = StringIO()
                with redirect_stdout(activate_output):
                    self.assertEqual(main(["activate", "community/gis-domain", "--from-router", "skillager-gis"]), 0)
                self.assertIn("# GIS Domain", activate_output.getvalue())

                self.assertEqual(main(["activate", "community/other", "--from-router", "skillager-gis"]), 2)

    def test_explicit_router_exposes_without_creating_tag_and_activates_listed_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            gis = collection / "gis-domain"
            other = collection / "other"
            gis.mkdir(parents=True)
            other.mkdir(parents=True)
            (gis / "SKILL.md").write_text("# GIS Domain\n\nUse GIS domain concepts.\n", encoding="utf-8")
            (other / "SKILL.md").write_text("# Other\n\nUse related support concepts.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--bulk-approve"]), 0)

                expose_output = StringIO()
                with redirect_stdout(expose_output):
                    self.assertEqual(
                        main(
                            [
                                "expose",
                                "community/gis-domain",
                                "community/other",
                                "--mode",
                                "router",
                                "--agent",
                                "codex",
                                "--json",
                            ]
                        ),
                        0,
                    )
                expose_data = json.loads(expose_output.getvalue())
                router_result = next(item for item in expose_data if item["skill_id"].startswith("skillager/router-"))
                router_slug = router_result["exposure_id"]
                self.assertTrue(router_slug.startswith("skillager-router-"))
                self.assertEqual(router_result["target"], str(root / ".agents" / "skills" / router_slug))

                tags_path = root / ".skillager" / "tags.json"
                if tags_path.exists():
                    self.assertEqual(json.loads(tags_path.read_text(encoding="utf-8")).get("tags"), {})

                router_dir = root / ".agents" / "skills" / router_slug
                router_text = (router_dir / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn(f"skillager activate <skill-id> --from-router {router_slug}", router_text)
                sidecar = load_mapping(router_dir / "skillager.materialized.yaml")
                self.assertEqual(sidecar["router_kind"], "explicit")
                self.assertEqual(sidecar["selection_kind"], "explicit")
                self.assertEqual(sidecar["router_slug"], router_slug)
                self.assertNotIn("tag", sidecar)
                self.assertEqual(sidecar["skill_ids"], ["community/gis-domain", "community/other"])

                search_output = StringIO()
                with redirect_stdout(search_output):
                    self.assertEqual(main(["search", "gis", "--json"]), 0)
                search_data = json.loads(search_output.getvalue())
                self.assertEqual(search_data[0]["exposure"], "router")
                self.assertEqual(search_data[0]["exposed_via"][0]["router_slug"], router_slug)
                self.assertNotIn("materialized_targets", search_data[0])

                activate_output = StringIO()
                with redirect_stdout(activate_output):
                    self.assertEqual(main(["activate", "community/gis-domain", "--from-router", router_slug]), 0)
                self.assertIn("# GIS Domain", activate_output.getvalue())

    def test_explicit_router_refuses_skill_not_listed_in_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            gis = collection / "gis-domain"
            other = collection / "other"
            gis.mkdir(parents=True)
            other.mkdir(parents=True)
            (gis / "SKILL.md").write_text("# GIS Domain\n\nUse GIS domain concepts.\n", encoding="utf-8")
            (other / "SKILL.md").write_text("# Other\n\nUse unrelated concepts.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--bulk-approve"]), 0)
                expose_output = StringIO()
                with redirect_stdout(expose_output):
                    self.assertEqual(main(["expose", "community/gis-domain", "--mode", "router", "--agent", "codex", "--json"]), 0)
                router_slug = json.loads(expose_output.getvalue())[0]["exposure_id"]

                error = StringIO()
                with redirect_stderr(error):
                    self.assertEqual(main(["activate", "community/other", "--from-router", router_slug]), 2)
                self.assertIn("skill community/other is not listed by router", error.getvalue())

    def test_explicit_router_skips_codex_incompatible_member_in_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            compatible = collection / "compatible"
            claude_only = collection / "claude-only"
            compatible.mkdir(parents=True)
            claude_only.mkdir(parents=True)
            (compatible / "SKILL.md").write_text("# Compatible\n\nUse compatible guidance.\n", encoding="utf-8")
            (claude_only / "SKILL.md").write_text("# Claude Only\n\nUse Claude-only guidance.\n", encoding="utf-8")
            (claude_only / "skillager.yaml").write_text(
                "\n".join(
                    [
                        "schema: skillager.skill.v1",
                        "audience:",
                        "  - user",
                        "activation:",
                        "  default: manual",
                        "compatibility:",
                        "  exclusive_to: claude",
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
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--bulk-approve"]), 0)

                expose_output = StringIO()
                with redirect_stdout(expose_output):
                    self.assertEqual(
                        main(
                            [
                                "expose",
                                "community/compatible",
                                "community/claude-only",
                                "--mode",
                                "router",
                                "--agent",
                                "codex",
                                "--json",
                            ]
                        ),
                        0,
                    )
                expose_data = json.loads(expose_output.getvalue())
                router_result = next(item for item in expose_data if item["skill_id"].startswith("skillager/router-"))
                skipped = next(item for item in expose_data if item["skill_id"] == "community/claude-only")
                self.assertEqual(router_result["status"], "exposed")
                self.assertEqual(skipped["status"], "skipped")
                self.assertIn("exclusive to claude", skipped["reason"])

                router_dir = root / ".agents" / "skills" / router_result["exposure_id"]
                sidecar = load_mapping(router_dir / "skillager.materialized.yaml")
                self.assertEqual(sidecar["skill_ids"], ["community/compatible"])
                router_text = (router_dir / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("community/compatible", router_text)
                self.assertNotIn("community/claude-only", router_text)

    def test_explicit_router_reexpose_skips_stale_members_and_preserves_remaining(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            gis = collection / "gis-domain"
            other = collection / "other"
            gis.mkdir(parents=True)
            other.mkdir(parents=True)
            (gis / "SKILL.md").write_text("# GIS Domain\n\nUse GIS domain concepts.\n", encoding="utf-8")
            (other / "SKILL.md").write_text("# Other\n\nUse related support concepts.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--bulk-approve"]), 0)
                initial_output = StringIO()
                explicit_expose = [
                    "expose",
                    "community/gis-domain",
                    "community/other",
                    "--mode",
                    "router",
                    "--agent",
                    "codex",
                    "--json",
                ]
                with redirect_stdout(initial_output):
                    self.assertEqual(main(explicit_expose), 0)
                router_slug = json.loads(initial_output.getvalue())[0]["exposure_id"]
                router_dir = root / ".agents" / "skills" / router_slug

                (gis / "SKILL.md").unlink()
                gis.rmdir()
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "refresh", "community"]), 0)

                reexpose_output = StringIO()
                with redirect_stdout(reexpose_output):
                    self.assertEqual(main(explicit_expose), 0)
                reexpose_data = json.loads(reexpose_output.getvalue())
                router_result = next(item for item in reexpose_data if item["skill_id"].startswith("skillager/router-"))
                skipped = next(item for item in reexpose_data if item["skill_id"] == "community/gis-domain")
                self.assertEqual(router_result["status"], "exposed")
                self.assertEqual(router_result["exposure_id"], router_slug)
                self.assertEqual(skipped["status"], "skipped")
                self.assertIn("skill not found", skipped["reason"])
                sidecar = load_mapping(router_dir / "skillager.materialized.yaml")
                self.assertEqual(sidecar["skill_ids"], ["community/other"])

                activate_other = StringIO()
                with redirect_stdout(activate_other):
                    self.assertEqual(main(["activate", "community/other", "--from-router", router_slug]), 0)
                self.assertIn("# Other", activate_other.getvalue())
                stale_error = StringIO()
                with redirect_stderr(stale_error):
                    self.assertEqual(main(["activate", "community/gis-domain", "--from-router", router_slug]), 2)
                self.assertIn("skill not found", stale_error.getvalue())

                before_all_stale_sidecar = (router_dir / "skillager.materialized.yaml").read_text(encoding="utf-8")
                (other / "SKILL.md").write_text("# Other\n\nUse changed support concepts.\n", encoding="utf-8")
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "refresh", "community"]), 0)
                all_stale_output = StringIO()
                with redirect_stdout(all_stale_output):
                    self.assertEqual(main(explicit_expose), 0)
                all_stale_data = json.loads(all_stale_output.getvalue())
                all_stale_router = next(item for item in all_stale_data if item["skill_id"].startswith("skillager/router-"))
                self.assertEqual(all_stale_router["status"], "skipped")
                self.assertIn("no available skills", all_stale_router["reason"])
                self.assertEqual((router_dir / "skillager.materialized.yaml").read_text(encoding="utf-8"), before_all_stale_sidecar)

                typo_error = StringIO()
                typo_slug = explicit_router_slug(["totally/missing"])
                with redirect_stderr(typo_error):
                    self.assertEqual(main(["expose", "totally/missing", "--mode", "router", "--agent", "codex"]), 2)
                self.assertIn("skill not found: totally/missing", typo_error.getvalue())
                self.assertFalse((root / ".agents" / "skills" / typo_slug).exists())

    def test_router_exposure_does_not_rewarn_for_approved_routed_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            skill_dir = collection / "gpu-review"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "# GPU Review\n\nUse GPU review guidance. Do not ask before checking every kernel.\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--bulk-approve"]), 0)
                    self.assertEqual(main(["tag", "create", "gpu"]), 0)
                    self.assertEqual(main(["tag", "add", "gpu", "community/gpu-review"]), 0)
                router_output = StringIO()
                with redirect_stdout(router_output):
                    self.assertEqual(main(["expose", "--tag", "gpu", "--mode", "router", "--agent", "codex"]), 0)
                tag_output = StringIO()
                with redirect_stdout(tag_output):
                    self.assertEqual(main(["tag", "show", "gpu", "--json"]), 0)
            text = router_output.getvalue()
            self.assertNotIn("Router scan note", text)
            self.assertNotIn("scanner finding", text)
            tag_data = json.loads(tag_output.getvalue())
            self.assertNotIn("scan", tag_data["skills"][0])
            self.assertNotIn("risk", tag_data["skills"][0])
            router = root / ".agents" / "skills" / "skillager-gpu" / "SKILL.md"
            router_text = router.read_text(encoding="utf-8")
            self.assertIn("community/gpu-review", router_text)
            self.assertNotIn("Risk:", router_text)

    def test_project_inventory_skill_can_be_tagged_and_routed_without_registered_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            skill_dir = root / "vibeSpatial" / ".agents" / "skills" / "gis-domain"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# GIS Domain\n\nUse GIS domain concepts.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                data = build_index(state, include_packages=False)
                skill = next(item for item in data["skills"] if item["id"] == "vibespatial/gis-domain")
                set_trust(state, skill["id"], "reviewed", skill["content_hash"], skill["source"])

                inventory_output = StringIO()
                with redirect_stdout(inventory_output):
                    self.assertEqual(main(["list", "--summary-json", "--agent", "codex"]), 0)
                inventory_data = json.loads(inventory_output.getvalue())
                self.assertEqual(inventory_data["skills"][0]["id"], "vibespatial/gis-domain")
                self.assertEqual(inventory_data["skills"][0]["tags"], [])
                self.assertEqual(inventory_data["counts"]["by_source"], {"collection": 1})

                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["tag", "add", "gis", "vibespatial/gis-domain"]), 0)
                    self.assertEqual(main(["expose", "--tag", "gis", "--mode", "router", "--agent", "codex"]), 0)

                router = root / ".agents" / "skills" / "skillager-gis" / "SKILL.md"
                router_text = router.read_text(encoding="utf-8")
                self.assertIn("vibespatial/gis-domain", router_text)

                search_output = StringIO()
                with redirect_stdout(search_output):
                    self.assertEqual(main(["search", "gis", "--tag", "gis", "--json"]), 0)
                search_data = json.loads(search_output.getvalue())
                self.assertEqual([item["id"] for item in search_data], ["vibespatial/gis-domain"])
                self.assertEqual(search_data[0]["exposure"], "router")

                activate_output = StringIO()
                with redirect_stdout(activate_output):
                    self.assertEqual(main(["activate", "vibespatial/gis-domain", "--from-router", "skillager-gis"]), 0)
                self.assertIn("# GIS Domain", activate_output.getvalue())

    def test_large_tag_router_is_search_driven_not_alphabetical_listing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            collection = root / "community"
            for index in range(21):
                skill = collection / f"skill-{index:02d}"
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(f"# Skill {index:02d}\n\nUse skill {index:02d} guidance.\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"SKILLAGER_STATE_DIR": str(state), "SKILLAGER_CATALOG_STATE_DIR": str(state), "NO_COLOR": "1"}),
                patch("skillager.discovery.find_project_root", return_value=root),
                patch("pathlib.Path.home", return_value=root),
                chdir(root),
            ):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["collection", "add", str(collection), "--name", "community"]), 0)
                    self.assertEqual(main(["setup", "--no-packages", "--source", "collection", "--accept-low"]), 0)
                    self.assertEqual(main(["collection", "enable", "community", "--tag", "all"]), 0)
                    self.assertEqual(main(["expose", "--tag", "all", "--mode", "router", "--agent", "codex"]), 0)
            router = root / ".agents" / "skills" / "skillager-all" / "SKILL.md"
            router_text = router.read_text(encoding="utf-8")
            self.assertIn("This tag contains 21 available skills.", router_text)
            self.assertIn('skillager search --tag all "<query>" --agent codex', router_text)
            self.assertNotIn("community/skill-00", router_text)

    def test_project_discovery_supports_common_skill_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / ".skillager"
            roots = [
                root / "skills" / "plain",
                root / ".claude" / "skills" / "claude",
                root / ".agents" / "skills" / "codex",
            ]
            for skill_dir in roots:
                skill_dir.mkdir(parents=True)
                (skill_dir / "SKILL.md").write_text(f"# {skill_dir.name.title()}\n\nUse guidance.\n", encoding="utf-8")
            with patch("skillager.discovery.find_project_root", return_value=root), patch("pathlib.Path.home", return_value=root):
                data = build_index(state, include_packages=False)
            skill_ids = {skill["id"] for skill in data["skills"]}
            self.assertIn("project/plain", skill_ids)
            self.assertIn("project/claude", skill_ids)
            self.assertIn("project/codex", skill_ids)


if __name__ == "__main__":
    unittest.main()
