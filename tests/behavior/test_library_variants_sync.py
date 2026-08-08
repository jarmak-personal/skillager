from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skillager.simple_yaml import dumps, load_mapping
from skillager.trust import content_hash

from .support import make_basic_workspace


PRIVATE_VARIANT_BODY = "PRIVATE VARIANT BODY MUST NOT LEAK"


class LibraryVariantsSyncBehaviorTests(unittest.TestCase):
    def assert_code(self, result, expected: int = 0) -> None:
        self.assertEqual(result.code, expected, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}")

    def create_library_skill(
        self,
        tmp: Path,
        *,
        name: str = "pandas",
        description: str = "Dataframe patterns for current pandas projects.",
    ):
        project, cli = make_basic_workspace(tmp)
        library = tmp / "library"
        self.assert_code(cli.run("library", "init", "--path", str(library)))
        self.assert_code(cli.run("library", "new", name))
        skill = library / "skills" / name
        skill.joinpath("SKILL.md").write_text(
            "---\n"
            f'name: "{name.title()}"\n'
            f'description: "{description}"\n'
            "---\n\n"
            f"# {name.title()}\n\n{PRIVATE_VARIANT_BODY}\n",
            encoding="utf-8",
        )
        accepted = cli.run("library", "accept", f"lib/{name}", "--yes", "--json")
        self.assert_code(accepted)
        return project, cli, library, skill, accepted.json()["skill"]["working_hash"]

    def test_fork_preview_is_read_only_and_valid_fork_has_distinct_metadata_and_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _project, cli, library, _skill, source_hash = self.create_library_skill(tmp)
            provenance_path = library / ".skillager" / "provenance.json"
            before_provenance = provenance_path.read_bytes()

            identical = cli.run(
                "fork",
                "lib/pandas",
                "--as",
                "pandas-current",
                "--description",
                "Dataframe patterns for current pandas projects.",
                "--json",
            )
            self.assert_code(identical, 2)
            self.assertIn("description must differ", identical.stderr)
            self.assertFalse((library / "skills" / "pandas-current").exists())

            preview = cli.run(
                "fork",
                "lib/pandas",
                "--as",
                "pandas-2",
                "--description",
                "Dataframe patterns for pandas 2.x codebases.",
                "--json",
            )
            self.assert_code(preview)
            payload = preview.json()
            self.assertEqual(payload["schema"], "skillager.library-fork.v1")
            self.assertEqual(payload["status"], "preview")
            self.assertEqual(payload["lineage"]["hash"], source_hash)
            self.assertEqual(payload["destination"]["name"], "Pandas 2")
            self.assertEqual(payload["destination"]["summary"], "Dataframe patterns for pandas 2.x codebases.")
            self.assertNotIn(PRIVATE_VARIANT_BODY, preview.stdout)
            self.assertFalse((library / "skills" / "pandas-2").exists())
            self.assertEqual(provenance_path.read_bytes(), before_provenance)

            forked = cli.run(
                "fork",
                "lib/pandas",
                "--as",
                "pandas-2",
                "--description",
                "Dataframe patterns for pandas 2.x codebases.",
                "--yes",
                "--json",
            )
            self.assert_code(forked)
            result = forked.json()
            self.assertEqual(result["status"], "forked")
            self.assertEqual(result["lineage"], {"skill": "lib/pandas", "hash": source_hash})
            self.assertEqual(result["destination"]["acceptance"], "accepted")
            self.assertEqual(result["destination"]["status"], "clean")
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))["skills"]["pandas-2"]
            self.assertEqual(provenance["forked_from"], result["lineage"])

            source = cli.run("show", "lib/pandas", "--json")
            variant = cli.run("show", "lib/pandas-2", "--json")
            self.assert_code(source)
            self.assert_code(variant)
            self.assertNotEqual(source.json()["skill"]["name"], variant.json()["skill"]["name"])
            self.assertNotEqual(source.json()["skill"]["summary"], variant.json()["skill"]["summary"])

    def test_historical_fork_records_the_exact_selected_hash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _project, cli, library, skill, first_hash = self.create_library_skill(tmp)
            skill.joinpath("SKILL.md").write_text(
                "---\n"
                'name: "Pandas"\n'
                'description: "Dataframe patterns for pandas 3.x codebases."\n'
                "---\n\n"
                f"# Pandas 3\n\n{PRIVATE_VARIANT_BODY}\n\nSecond generation.\n",
                encoding="utf-8",
            )
            second = cli.run("library", "accept", "lib/pandas", "--yes", "--json")
            self.assert_code(second)
            self.assertNotEqual(second.json()["skill"]["working_hash"], first_hash)

            result = cli.run(
                "fork",
                "lib/pandas",
                "--as",
                "pandas-legacy",
                "--description",
                "Dataframe patterns for legacy pandas 1.x projects.",
                "--from",
                first_hash[:12],
                "--yes",
                "--json",
            )
            self.assert_code(result)
            payload = result.json()
            self.assertEqual(payload["source"]["kind"], "history")
            self.assertEqual(payload["lineage"]["hash"], first_hash)
            fork_body = (library / "skills" / "pandas-legacy" / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("Second generation", fork_body)
            self.assertIn("legacy pandas 1.x", fork_body)

    def test_fork_description_quotes_round_trip_in_agent_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _project, cli, _library, _skill, _source_hash = self.create_library_skill(tmp)
            description = 'Use the "legacy" pandas workflow on Windows paths like C:\\data.'
            result = cli.run(
                "fork",
                "lib/pandas",
                "--as",
                "pandas-windows",
                "--description",
                description,
                "--yes",
                "--json",
            )
            self.assert_code(result)
            self.assertEqual(result.json()["destination"]["summary"], description)
            shown = cli.run("show", "lib/pandas-windows", "--json")
            self.assert_code(shown)
            self.assertEqual(shown.json()["skill"]["summary"], description)

    def test_sync_is_preview_first_updates_only_clean_unpinned_exposures_and_preserves_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            project, cli, _library, skill, first_hash = self.create_library_skill(tmp, name="brainstorm")
            self.assert_code(cli.run("expose", "lib/brainstorm", "--agent", "codex"))
            self.assert_code(cli.run("expose", "lib/brainstorm", "--mode", "stub", "--agent", "claude"))
            native = project / ".agents" / "skills" / "lib-brainstorm"
            stub = project / ".claude" / "skills" / "lib-brainstorm"
            native_before = native.joinpath("SKILL.md").read_bytes()
            stub_before = stub.joinpath("SKILL.md").read_bytes()
            native_library_id = load_mapping(native / "skillager.materialized.yaml")["source_library_id"]
            stub_library_id = load_mapping(stub / "skillager.materialized.yaml")["source_library_id"]

            skill.joinpath("SKILL.md").write_text(
                "---\n"
                'name: "Brainstorm"\n'
                'description: "Improved canonical brainstorming for product teams."\n'
                "---\n\n"
                f"# Brainstorm\n\n{PRIVATE_VARIANT_BODY}\n\nSecond version.\n",
                encoding="utf-8",
            )
            accepted = cli.run("library", "accept", "lib/brainstorm", "--yes", "--json")
            self.assert_code(accepted)
            second_hash = accepted.json()["skill"]["working_hash"]
            self.assertNotEqual(first_hash, second_hash)

            pinned = cli.run("pin", "lib/brainstorm", "--agent", "codex", "--json")
            self.assert_code(pinned)
            self.assertEqual(pinned.json()["pin_hash"], first_hash)
            reconcile = cli.run("reconcile", "lib/brainstorm", "--agent", "codex", "--json")
            self.assert_code(reconcile)
            source = reconcile.json()["items"][0]["source"]
            self.assertEqual(source["status"], "behind")
            self.assertTrue(source["pinned"])

            preview = cli.run("sync", "--json")
            self.assert_code(preview)
            payload = preview.json()
            self.assertTrue(payload["read_only"])
            reasons = {(item["agent"], item["reason"]) for item in payload["items"]}
            self.assertIn(("codex", "pinned"), reasons)
            self.assertIn(("claude", "behind"), reasons)
            self.assertEqual(native.joinpath("SKILL.md").read_bytes(), native_before)
            self.assertEqual(stub.joinpath("SKILL.md").read_bytes(), stub_before)

            applied = cli.run("sync", "--apply", "--json")
            self.assert_code(applied)
            self.assertEqual(applied.json()["counts"]["updated"], 1)
            self.assertEqual(native.joinpath("SKILL.md").read_bytes(), native_before)
            stub_sidecar = load_mapping(stub / "skillager.materialized.yaml")
            self.assertEqual(stub_sidecar["source_hash"], second_hash)
            self.assertEqual(stub_sidecar["source_library_id"], stub_library_id)
            self.assertIn("Improved canonical brainstorming", stub.joinpath("SKILL.md").read_text(encoding="utf-8"))

            unpinned = cli.run("unpin", "lib/brainstorm", "--agent", "codex", "--json")
            self.assert_code(unpinned)
            self.assertEqual(unpinned.json()["previous_pin_hash"], first_hash)
            updated = cli.run("sync", "--agent", "codex", "--apply", "--json")
            self.assert_code(updated)
            self.assertEqual(updated.json()["counts"]["updated"], 1)
            native_sidecar = load_mapping(native / "skillager.materialized.yaml")
            self.assertEqual(native_sidecar["source_hash"], second_hash)
            self.assertEqual(native_sidecar["source_library_id"], native_library_id)
            self.assertEqual(content_hash(native), second_hash)

    def test_sync_reports_stable_skip_reasons_and_never_walks_sibling_projects(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            project, cli, _library, skill, _first_hash = self.create_library_skill(tmp, name="safe")
            self.assert_code(cli.run("expose", "lib/safe", "--agent", "codex"))
            target = project / ".agents" / "skills" / "lib-safe"
            target.joinpath("SKILL.md").write_text("# Local Edit\n\nUnsaved work.\n", encoding="utf-8")

            external = project / ".skills" / "external"
            external.mkdir(parents=True)
            external.joinpath("SKILL.md").write_text("# External\n\nReviewed external workflow.\n", encoding="utf-8")
            self.assert_code(cli.run("setup", "--source", "project", "--accept-low", "--no-packages"))
            self.assert_code(cli.run("expose", "project/external", "--agent", "claude"))

            skill.joinpath("SKILL.md").write_text("# Pending\n\nUnaccepted source edit.\n", encoding="utf-8")
            sibling = tmp / "sibling-project"
            sibling_target = sibling / ".agents" / "skills" / "lib-safe"
            sibling_target.mkdir(parents=True)
            sibling_target.joinpath("SKILL.md").write_text("# Sibling\n\nMust remain untouched.\n", encoding="utf-8")
            sibling_before = sibling_target.joinpath("SKILL.md").read_bytes()

            preview = cli.run("sync", "--json")
            self.assert_code(preview)
            reasons = {item["reason"] for item in preview.json()["items"]}
            self.assertIn("dirty", reasons)
            self.assertIn("external-source", reasons)
            self.assertNotIn(PRIVATE_VARIANT_BODY, preview.stdout)

            applied = cli.run("sync", "--apply", "--json")
            self.assert_code(applied)
            self.assertEqual(applied.json()["update_count"], 0)
            self.assertEqual(sibling_target.joinpath("SKILL.md").read_bytes(), sibling_before)

            target.joinpath("SKILL.md").write_text(skill.joinpath("SKILL.md").read_text(encoding="utf-8"), encoding="utf-8")
            sidecar = load_mapping(target / "skillager.materialized.yaml")
            sidecar["materialized_hash"] = content_hash(target)
            target.joinpath("skillager.materialized.yaml").write_text(dumps(sidecar), encoding="utf-8")
            target.joinpath("draft.tmp").write_text("preserve excluded local bytes\n", encoding="utf-8")
            unresolved = cli.run("sync", "--agent", "codex", "--json")
            self.assert_code(unresolved)
            safe_item = next(item for item in unresolved.json()["items"] if item["skill_id"] == "lib/safe")
            self.assertEqual(safe_item["reason"], "unresolved-drift")
            self.assertTrue(target.joinpath("draft.tmp").is_file())

    def test_pin_to_a_different_version_refuses_without_rewriting_the_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            project, cli, _library, skill, first_hash = self.create_library_skill(tmp, name="frozen")
            self.assert_code(cli.run("expose", "lib/frozen", "--agent", "codex"))
            target = project / ".agents" / "skills" / "lib-frozen"
            before = target.joinpath("SKILL.md").read_bytes()
            skill.joinpath("SKILL.md").write_text("# Frozen\n\nSecond accepted version.\n", encoding="utf-8")
            second = cli.run("library", "accept", "lib/frozen", "--yes", "--json")
            self.assert_code(second)
            second_hash = second.json()["skill"]["working_hash"]

            refused = cli.run("pin", "lib/frozen", "--agent", "codex", "--to", second_hash, "--json")
            self.assert_code(refused, 2)
            self.assertIn("must identify the exposure's current source hash", refused.stderr)
            self.assertEqual(target.joinpath("SKILL.md").read_bytes(), before)
            self.assertNotIn("pin_hash", load_mapping(target / "skillager.materialized.yaml"))

            pinned = cli.run("pin", "lib/frozen", "--agent", "codex", "--to", first_hash[:12], "--json")
            self.assert_code(pinned)
            self.assertEqual(pinned.json()["pin_hash"], first_hash)

    def test_sync_quarantines_every_unsafe_state_with_a_stable_skip_reason(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            project, cli, library, _skill, _source_hash = self.create_library_skill(tmp, name="seed")

            targets: dict[str, Path] = {}
            for name in ("customized", "blocked", "missing", "malformed", "unaccepted"):
                self.assert_code(cli.run("library", "new", name))
                source = library / "skills" / name
                source.joinpath("SKILL.md").write_text(
                    f"# {name.title()}\n\nCanonical {name} workflow.\n",
                    encoding="utf-8",
                )
                self.assert_code(cli.run("library", "accept", f"lib/{name}", "--yes"))
                self.assert_code(cli.run("expose", f"lib/{name}", "--agent", "codex"))
                targets[name] = project / ".agents" / "skills" / f"lib-{name}"

            targets["customized"].joinpath("SKILL.md").write_text(
                "# Customized\n\nDeliberate project variant.\n",
                encoding="utf-8",
            )
            self.assert_code(
                cli.run("reconcile", "keep-local", "lib/customized", "--agent", "codex", "--yes")
            )

            blocked_sidecar = load_mapping(targets["blocked"] / "skillager.materialized.yaml")
            blocked_sidecar["exposure_blocked_hashes"] = [blocked_sidecar["materialized_hash"]]
            targets["blocked"].joinpath("skillager.materialized.yaml").write_text(
                dumps(blocked_sidecar),
                encoding="utf-8",
            )
            targets["missing"].joinpath("SKILL.md").unlink()
            targets["malformed"].joinpath("skillager.materialized.yaml").write_text(
                "schema: unsupported.sidecar\n",
                encoding="utf-8",
            )
            (library / "skills" / "unaccepted" / "SKILL.md").write_text(
                "# Unaccepted\n\nPending canonical edit.\n",
                encoding="utf-8",
            )

            preview = cli.run("sync", "--agent", "codex", "--json")
            self.assert_code(preview)
            by_id = {item["skill_id"]: item["reason"] for item in preview.json()["items"]}
            self.assertEqual(by_id["lib/customized"], "customized")
            self.assertEqual(by_id["lib/blocked"], "blocked")
            self.assertEqual(by_id["lib/missing"], "target-missing")
            self.assertEqual(by_id["lib/unaccepted"], "unaccepted-source")
            malformed = [
                item for item in preview.json()["items"] if item["target"] == str(targets["malformed"].resolve())
            ]
            self.assertEqual(malformed[0]["reason"], "malformed-sidecar")

            applied = cli.run("sync", "--agent", "codex", "--apply", "--json")
            self.assert_code(applied)
            self.assertEqual(applied.json()["update_count"], 0)
            self.assertIn("Deliberate project variant", targets["customized"].joinpath("SKILL.md").read_text(encoding="utf-8"))
            self.assertFalse(targets["missing"].joinpath("SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
