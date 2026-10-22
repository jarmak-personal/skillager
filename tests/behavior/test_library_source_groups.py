"""Source grouping is a complete metadata browse, not an approval or import operation."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from .support import BODY_SENTINEL, make_basic_workspace, write_basic_skill


class LibrarySourceGroupBehaviorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skillager-source-groups-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project, self.cli = make_basic_workspace(self.root)
        self.library = self.root / "library"
        self.checked(self.cli.run("library", "init", "--path", str(self.library), "--no-git", "--json"))

    def checked(self, result, code=0):
        self.assertEqual(result.code, code, result.stdout + result.stderr)
        self.assertNotIn(BODY_SENTINEL, result.stdout + result.stderr)
        return result.json()

    def listing(self, *args, code=0):
        return self.checked(self.cli.run("list", "--scope", "library", "--json", *args), code)

    def imported(self, name):
        write_basic_skill(self.cli.project, name)
        return self.checked(self.cli.run_confirmed("import", f"project/{name}", "--yes", "--json"))

    def test_full_counts_filtered_pages_and_same_named_projects_remain_distinct(self):
        self.imported("first")
        self.imported("second")
        other = self.root / "elsewhere" / self.project.name
        other.mkdir(parents=True)
        (other / "pyproject.toml").write_text('[project]\nname="other"\n')
        self.cli.project = other
        self.imported("third")
        self.checked(self.cli.run("library", "new", "draft", "--json"))
        self.checked(self.cli.run("library", "new", "blocked", "--json"))
        (self.library / "skills" / "blocked" / "skillager.yaml").write_text("schema: unsupported\n")
        first = self.listing("--group-by", "source", "--limit", "1")
        self.assertEqual(first["schema"], "skillager.library-groups.v1")
        self.assertEqual((first["total_skills"], first["total_groups"], first["discovery_error_count"]), (5, 3, 1))
        groups = first["groups"][:]
        cursor = first["next_cursor"]
        while cursor:
            page = self.listing("--group-by", "source", "--limit", "1", "--cursor", cursor)
            groups += page["groups"]
            cursor = page["next_cursor"]
        projects = [group for group in groups if group["kind"] == "project"]
        self.assertEqual([group["label"] for group in projects], ["project", "project"])
        self.assertEqual({group["detail"] for group in projects}, {str(self.project), str(other)})
        self.assertEqual(len({group["id"] for group in projects}), 2)
        group = next(group for group in projects if group["count"] == 2)
        page = self.listing("--source-group", group["id"], "--limit", "1")
        self.assertEqual(page["total_skills"], 2)
        self.assertEqual([row["id"] for row in page["skills"]], ["lib/first"])
        self.assertEqual(page["skills"][0]["source_group"], {key: value for key, value in group.items() if key != "count"})
        last = self.listing("--source-group", group["id"], "--limit", "1", "--cursor", page["next_cursor"])
        self.assertEqual([row["id"] for row in last["skills"]], ["lib/second"])
        self.assertIsNone(last["next_cursor"])
        changed_request = self.listing("--source-group", projects[0]["id"] if projects[0]["id"] != group["id"] else projects[1]["id"],
                                       "--limit", "1", "--cursor", page["next_cursor"], code=2)
        self.assertEqual(changed_request["error"]["code"], "invalid_cursor")
        # A hidden/unrelated group's change still invalidates the traversal.
        self.checked(self.cli.run("library", "new", "another-draft", "--json"))
        stale = self.listing("--source-group", group["id"], "--limit", "1", "--cursor", page["next_cursor"], code=15)
        self.assertEqual(stale["error"]["code"], "stale_cursor")

    def test_import_source_survives_original_removal_edit_and_reacceptance(self):
        imported = self.imported("saved")
        row = self.listing()["skills"][0]
        group = row["source_group"]
        shutil.rmtree(self.project / ".skills")
        canonical = self.library / "skills" / "saved" / "SKILL.md"
        canonical.write_text(canonical.read_text() + "\nLocal improvement.\n")
        pending = self.listing()["skills"][0]
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["source_group"], group)
        accepted = self.checked(self.cli.run_confirmed("library", "accept", "saved", "--yes", "--json"))
        current = self.listing()["skills"][0]
        self.assertEqual(current["source_group"], group)
        self.assertEqual(current["accepted_hash"], accepted["skill"]["working_hash"])
        self.assertNotEqual(current["accepted_hash"], imported["destination"]["accepted_hash"])

    def test_historical_import_and_sync_origins_are_used_without_rewriting_metadata(self):
        self.imported("imported")
        write_basic_skill(self.project, "synced")
        approved = self.checked(self.cli.run("review", "approve", "project/synced", "--project-only", "--json"))
        synced = approved["action"]["library_sync"]["items"][0]["canonical_skill_id"]
        provenance_path = self.library / ".skillager" / "provenance.json"
        provenance = json.loads(provenance_path.read_text())
        for entry in provenance["skills"].values():
            entry.pop("source_group", None)
        provenance_path.write_text(json.dumps(provenance))
        before = provenance_path.read_bytes()
        shutil.rmtree(self.project / ".skills")
        rows = self.listing()["skills"]
        self.assertEqual({row["id"] for row in rows}, {"lib/imported", synced})
        self.assertEqual({row["source_group"]["label"] for row in rows}, {"project"})
        self.assertEqual(len({row["source_group"]["id"] for row in rows}), 1)
        self.assertEqual(provenance_path.read_bytes(), before)

    def test_search_group_filter_precedes_ranking_paging_without_granting_pending_access(self):
        self.imported("alpha")
        self.imported("beta")
        group = self.listing()["skills"][0]["source_group"]
        self.checked(self.cli.run("library", "new", "personal", "--json"))
        self.checked(self.cli.run_confirmed("library", "accept", "personal", "--yes", "--json"))
        # Editing a member leaves it visible to browse but unavailable to search.
        canonical = self.library / "skills" / "beta" / "SKILL.md"
        canonical.write_text(canonical.read_text() + "\nPending change.\n")
        for view in ([], ["--view", "skills", "--include-installed"]):
            args = ["search", "", "--scope", "library", "--json", "--cursor", "", "--limit", "1", *view,
                    "--source-group", group["id"]]
            result = self.checked(self.cli.run(*args))
            self.assertEqual([row["id"] for row in result["results"]], ["lib/alpha"])
            self.assertEqual(result["results"][0]["source_group"], group)
            self.assertIsNone(result["next_cursor"])
        self.assertEqual(self.listing("--source-group", group["id"])["total_skills"], 2)

    def test_multiple_stored_sync_origins_remain_visible_without_becoming_authority(self):
        write_basic_skill(self.project, "synced")
        self.checked(self.cli.run("review", "approve", "project/synced", "--project-only", "--json"))
        provenance_path = self.library / ".skillager" / "provenance.json"
        provenance = json.loads(provenance_path.read_text())
        record = next(iter(provenance["skills"].values()))
        original = record["sync"]["origins"][0]
        other = {**original, "origin_id": "b" * 64, "path": str(self.root / "elsewhere" / "skill"),
                 "native": {"agent": "codex", "scope": "project", "project_root": str(self.root / "elsewhere")}}
        record["sync"]["origins"].append(other)
        provenance_path.write_text(json.dumps(provenance))
        row = self.listing()["skills"][0]
        self.assertEqual(row["source_group"]["kind"], "multiple")
        self.assertEqual(row["source_group"]["label"], "elsewhere + project")
        self.assertIn(str(self.project), row["source_group"]["detail"])
        self.assertIn(str(self.root / "elsewhere"), row["source_group"]["detail"])
        self.assertEqual(row["status"], "accepted")
        # Source presentation does not repair or authorize this edited lineage.
        status = self.checked(self.cli.run("library", "sync", "--status", "--json"))
        self.assertNotEqual(status["lineages"][0]["preservation"], "verified")

    def test_same_named_package_installations_are_distinct_and_import_matches_sync(self):
        installations = [self.project / "node_modules" / name for name in ("first-install", "second-install")]
        for number, installation in enumerate(installations, start=1):
            installation.mkdir(parents=True)
            (installation / "package.json").write_text(json.dumps({"name": "toolkit", "version": f"{number}.0.0"}))
            write_basic_skill(installation, f"help-{number}")
            self.checked(self.cli.run_confirmed("import", f"toolkit/help-{number}", "--yes", "--json"))
        write_basic_skill(installations[0], "synced-help")
        approved = self.checked(self.cli.run("review", "approve", "toolkit/synced-help", "--json"))
        self.assertEqual(approved["action"]["library_sync"]["counts"]["created"], 1)
        shutil.rmtree(self.project / "node_modules")
        groups = self.listing("--group-by", "source")["groups"]
        self.assertEqual([group["label"] for group in groups], ["toolkit", "toolkit"])
        self.assertEqual([group["kind"] for group in groups], ["package", "package"])
        self.assertEqual({group["detail"] for group in groups}, set(map(str, installations)))
        self.assertEqual(sorted(group["count"] for group in groups), [1, 2])
        self.assertEqual(len({group["id"] for group in groups}), 2)

    def test_empty_unknown_group_invalid_options_and_metadata_only_fallbacks(self):
        empty = self.listing("--group-by", "source")
        self.assertEqual((empty["groups"], empty["total_groups"], empty["total_skills"]), ([], 0, 0))
        self.checked(self.cli.run("library", "new", "created", "--json"))
        self.checked(self.cli.run("library", "new", "historical", "--json"))
        provenance_path = self.library / ".skillager" / "provenance.json"
        provenance = json.loads(provenance_path.read_text())
        provenance["skills"]["historical"] = {"imported_from": {"skill_id": "project/original", "source_type": "project", "content_hash": "a" * 64}}
        provenance_path.write_text(json.dumps(provenance))
        groups = self.listing("--group-by", "source")["groups"]
        self.assertEqual({group["label"] for group in groups}, {"Personal", "Unknown source"})
        self.assertEqual(self.listing("--source-group", "source:" + "0" * 64)["skills"], [])
        for args in (("list", "--group-by", "source", "--json"),
                     ("list", "--scope", "library", "--group-by", "source", "--limit", "101", "--json"),
                     ("list", "--scope", "library", "--source-group", "invalid", "--json"),
                     ("search", "", "--source-group", groups[0]["id"], "--json")):
            result = self.cli.run(*args)
            self.assertEqual(result.code, 2, result.stdout + result.stderr)
