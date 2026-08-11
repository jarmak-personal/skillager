from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from skillager.exposure.impl import materialize_working_skill
from skillager.simple_yaml import load_mapping
from tests.behavior.support import CliResult, make_basic_workspace


class ExposureHardeningBehaviorTests(unittest.TestCase):
    def assert_code(self, result: CliResult, expected: int) -> None:
        self.assertEqual(
            result.code,
            expected,
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}",
        )

    def test_native_projection_rejects_manifest_free_external_skill_but_stub_remains_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, cli = make_basic_workspace(root)
            source = project / ".skills" / "plain-external"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text(
                "# Plain External\n\nUse this external workflow.\n",
                encoding="utf-8",
            )
            self.assert_code(
                cli.run("setup", "--source", "project", "--accept-low", "--non-interactive", "--json"),
                0,
            )

            native = cli.run("expose", "project/plain-external", "--mode", "native", "--agent", "codex", "--json")
            self.assert_code(native, 0)
            self.assertEqual(native.json()[0]["status"], "skipped")
            self.assertIn("frontmatter", native.json()[0]["reason"])
            self.assertFalse((project / ".agents" / "skills" / "project-plain-external").exists())

            stub = cli.run("expose", "project/plain-external", "--mode", "stub", "--agent", "codex", "--json")
            self.assert_code(stub, 0)
            self.assertEqual(stub.json()[0]["status"], "exposed")
            self.assertTrue((project / ".agents" / "skills" / "project-plain-external" / "SKILL.md").is_file())

    def test_native_and_stub_slug_collisions_allocate_distinct_targets(self) -> None:
        for mode in ("native", "stub"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                project, cli = make_basic_workspace(root)
                first_root = root / "first-collection"
                second_root = root / "second-collection"
                self._write_skill(first_root / "a" / "b", "First collision body")
                self._write_skill(second_root / "b", "Second collision body")
                self.assert_code(cli.run("collection", "add", str(first_root), "--name", "team"), 0)
                self.assert_code(cli.run("collection", "add", str(second_root), "--name", "team-a"), 0)
                self.assert_code(cli.run("review", "approve", "team/a/b"), 0)
                self.assert_code(cli.run("review", "approve", "team-a/b"), 0)

                first = cli.run("expose", "team/a/b", "--mode", mode, "--agent", "codex", "--json")
                second = cli.run("expose", "team-a/b", "--mode", mode, "--agent", "codex", "--json")
                self.assert_code(first, 0)
                self.assert_code(second, 0)
                self.assertEqual(first.json()[0]["status"], "exposed")
                self.assertEqual(second.json()[0]["status"], "exposed")

                base = project / ".agents" / "skills" / "team-a-b"
                suffix = hashlib.sha256(b"team-a/b").hexdigest()[:8]
                alternate = base.with_name(f"team-a-b-{suffix}")
                self.assertTrue((base / "SKILL.md").is_file())
                self.assertTrue((alternate / "SKILL.md").is_file())
                self.assertEqual(load_mapping(base / "skillager.materialized.yaml")["source_id"], "team/a/b")
                self.assertEqual(load_mapping(alternate / "skillager.materialized.yaml")["source_id"], "team-a/b")
                if mode == "native":
                    self.assertIn("First collision body", (base / "SKILL.md").read_text(encoding="utf-8"))
                    self.assertIn("Second collision body", (alternate / "SKILL.md").read_text(encoding="utf-8"))
                else:
                    first_activated = cli.run(
                        "activate",
                        "team/a/b",
                        "--from-stub",
                        base.name,
                        "--no-session-record",
                    )
                    self.assert_code(first_activated, 0)
                    self.assertIn("First collision body", first_activated.stdout)
                    activated = cli.run(
                        "activate",
                        "team-a/b",
                        "--from-stub",
                        alternate.name,
                        "--no-session-record",
                    )
                    self.assert_code(activated, 0)
                    self.assertIn("Second collision body", activated.stdout)

    def test_occupied_collision_fallback_fails_closed_without_force_or_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, cli = make_basic_workspace(root)
            first_root = root / "first-collection"
            second_root = root / "second-collection"
            self._write_skill(first_root / "a" / "b", "First collision body")
            self._write_skill(second_root / "b", "Second collision body")
            self.assert_code(cli.run("collection", "add", str(first_root), "--name", "team"), 0)
            self.assert_code(cli.run("collection", "add", str(second_root), "--name", "team-a"), 0)
            self.assert_code(cli.run("review", "approve", "team/a/b"), 0)
            self.assert_code(cli.run("review", "approve", "team-a/b"), 0)
            self.assert_code(cli.run("expose", "team/a/b", "--mode", "stub", "--agent", "codex", "--json"), 0)

            base = project / ".agents" / "skills" / "team-a-b"
            suffix = hashlib.sha256(b"team-a/b").hexdigest()[:8]
            alternate = base.with_name(f"team-a-b-{suffix}")
            alternate.mkdir(parents=True)
            sentinel = alternate / "KEEP.txt"
            sentinel.write_text("do not overwrite\n", encoding="utf-8")

            for force in (False, True):
                args = ["expose", "team-a/b", "--mode", "stub", "--agent", "codex", "--json"]
                if force:
                    args.append("--force")
                refused = cli.run(*args)
                self.assert_code(refused, 0)
                self.assertEqual(refused.json()[0]["status"], "skipped")
                self.assertIn("collision fallback target is occupied", refused.json()[0]["reason"])
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not overwrite\n")
                self.assertFalse((alternate / "SKILL.md").exists())
            self.assertEqual(load_mapping(base / "skillager.materialized.yaml")["source_id"], "team/a/b")

    def test_working_and_same_slug_tag_router_preserve_managed_identity_in_both_orders(self) -> None:
        for router_first in (False, True):
            with self.subTest(router_first=router_first), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                project, cli = make_basic_workspace(root)
                self._write_skill(project / ".skills" / "member", "Working collision member")
                setup_args = [
                    "setup",
                    "--source",
                    "project",
                    "--accept-low",
                    "--no-packages",
                    "--non-interactive",
                    "--json",
                ]
                if not router_first:
                    setup_args.extend(("--agent", "codex"))
                self.assert_code(cli.run(*setup_args), 0)
                self.assert_code(cli.run("tag", "create", "working"), 0)
                self.assert_code(cli.run("tag", "add", "working", "project/member"), 0)

                reserved = project / ".agents" / "skills" / "skillager-working"
                if router_first:
                    router = cli.run(
                        "expose",
                        "--tag",
                        "working",
                        "--mode",
                        "router",
                        "--agent",
                        "codex",
                        "--json",
                    )
                    self.assert_code(router, 0)
                    self.assertEqual(router.json()[0]["exposure_id"], reserved.name)
                    before_body = (reserved / "SKILL.md").read_text(encoding="utf-8")
                    before_sidecar = (reserved / "skillager.materialized.yaml").read_text(encoding="utf-8")

                    setup = cli.run(*setup_args, "--agent", "codex")
                    self.assert_code(setup, 0)
                    artifact = setup.json()["working_artifacts"]["artifacts"][0]
                    self.assertEqual(artifact["status"], "skipped")
                    self.assertIn("reserved projection target is occupied", artifact["reason"])
                    self.assertEqual((reserved / "SKILL.md").read_text(encoding="utf-8"), before_body)
                    self.assertEqual(
                        (reserved / "skillager.materialized.yaml").read_text(encoding="utf-8"),
                        before_sidecar,
                    )
                    forced = materialize_working_skill(
                        agents=["codex"],
                        project_dir=project,
                        force=True,
                    )
                    self.assertEqual(forced[0]["status"], "skipped")
                    self.assertIn("reserved projection target is occupied", forced[0]["reason"])
                    self.assertEqual((reserved / "SKILL.md").read_text(encoding="utf-8"), before_body)
                    self.assertEqual(
                        (reserved / "skillager.materialized.yaml").read_text(encoding="utf-8"),
                        before_sidecar,
                    )
                else:
                    self.assertEqual(
                        load_mapping(reserved / "skillager.materialized.yaml")["source_type"],
                        "skillager-working",
                    )
                    before_body = (reserved / "SKILL.md").read_text(encoding="utf-8")
                    self.assertIn("`source_update` means", before_body)
                    self.assertIn("re-expose only", before_body)
                    self.assertIn("`source_unavailable` means", before_body)
                    self.assertIn("no re-expose command is valid", before_body)
                    router = cli.run(
                        "expose",
                        "--tag",
                        "working",
                        "--mode",
                        "router",
                        "--agent",
                        "codex",
                        "--force",
                        "--json",
                    )
                    self.assert_code(router, 0)
                    self.assertEqual(router.json()[0]["status"], "exposed")
                    router_target = Path(router.json()[0]["target"])
                    self.assertNotEqual(router_target, reserved)
                    self.assertTrue(router_target.name.startswith("skillager-working-"))
                    router_sidecar = load_mapping(router_target / "skillager.materialized.yaml")
                    self.assertEqual(router_sidecar["router_slug"], router_target.name)
                    self.assertEqual(router_sidecar["projection_kind"], "router-tag")
                    self.assertIn(
                        f"--from-router {router_target.name}",
                        (router_target / "SKILL.md").read_text(encoding="utf-8"),
                    )
                    self.assertEqual((reserved / "SKILL.md").read_text(encoding="utf-8"), before_body)

    def test_direct_and_tag_router_slug_collisions_preserve_both_in_both_orders(self) -> None:
        for router_first in (False, True):
            with self.subTest(router_first=router_first), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                project, cli = make_basic_workspace(root)
                collection = root / "collection"
                self._write_skill(collection / "foo", "Direct collision body")
                self._write_skill(project / ".skills" / "member", "Tag router member")
                self.assert_code(cli.run("collection", "add", str(collection), "--name", "skillager-gis"), 0)
                self.assert_code(cli.run("review", "approve", "skillager-gis/foo"), 0)
                self.assert_code(
                    cli.run(
                        "setup",
                        "--source",
                        "project",
                        "--accept-low",
                        "--no-packages",
                        "--non-interactive",
                        "--json",
                    ),
                    0,
                )
                self.assert_code(cli.run("tag", "create", "gis-foo"), 0)
                self.assert_code(cli.run("tag", "add", "gis-foo", "project/member"), 0)

                direct_args = ("expose", "skillager-gis/foo", "--mode", "stub", "--agent", "codex", "--json")
                router_args = ("expose", "--tag", "gis-foo", "--mode", "router", "--agent", "codex", "--json")
                first = cli.run(*(router_args if router_first else direct_args))
                second = cli.run(*(direct_args if router_first else router_args), "--force")
                self.assert_code(first, 0)
                self.assert_code(second, 0)
                self.assertEqual(first.json()[0]["status"], "exposed")
                self.assertEqual(second.json()[0]["status"], "exposed")

                base = project / ".agents" / "skills" / "skillager-gis-foo"
                alternate = Path(second.json()[0]["target"])
                self.assertNotEqual(alternate, base)
                base_sidecar = load_mapping(base / "skillager.materialized.yaml")
                alternate_sidecar = load_mapping(alternate / "skillager.materialized.yaml")
                by_kind = {
                    base_sidecar["projection_kind"]: (base, base_sidecar),
                    alternate_sidecar["projection_kind"]: (alternate, alternate_sidecar),
                }
                self.assertEqual(set(by_kind), {"direct", "router-tag"})
                router_target, router_sidecar = by_kind["router-tag"]
                self.assertEqual(router_sidecar["router_slug"], router_target.name)
                self.assertIn(
                    f"--from-router {router_target.name}",
                    (router_target / "SKILL.md").read_text(encoding="utf-8"),
                )
                repeated = cli.run(
                    "expose",
                    "--tag",
                    "GIS_FOO",
                    "--mode",
                    "router",
                    "--agent",
                    "codex",
                    "--force",
                    "--json",
                )
                self.assert_code(repeated, 0)
                self.assertEqual(Path(repeated.json()[0]["target"]).resolve(), router_target.resolve())
                routed = cli.run(
                    "activate",
                    "project/member",
                    "--from-router",
                    router_target.name,
                    "--no-session-record",
                )
                self.assert_code(routed, 0)
                self.assertIn("Tag router member", routed.stdout)
                direct_target, direct_sidecar = by_kind["direct"]
                self.assertEqual(direct_sidecar["source_id"], "skillager-gis/foo")
                activated = cli.run(
                    "activate",
                    "skillager-gis/foo",
                    "--from-stub",
                    direct_target.name,
                    "--no-session-record",
                )
                self.assert_code(activated, 0)
                self.assertIn("Direct collision body", activated.stdout)

    def test_direct_and_explicit_router_slug_collision_preserves_actual_router_slug(self) -> None:
        for router_first in (False, True):
            with self.subTest(router_first=router_first), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                project, cli = make_basic_workspace(root)
                self._write_skill(project / ".skills" / "member", "Explicit router member")
                self.assert_code(
                    cli.run(
                        "setup",
                        "--source",
                        "project",
                        "--accept-low",
                        "--no-packages",
                        "--non-interactive",
                        "--json",
                    ),
                    0,
                )
                digest = hashlib.sha256(b"project/member").hexdigest()[:10]
                collection = root / "collection"
                self._write_skill(collection / digest, "Explicit direct collision body")
                self.assert_code(cli.run("collection", "add", str(collection), "--name", "skillager-router"), 0)
                self.assert_code(cli.run("review", "approve", f"skillager-router/{digest}"), 0)
                direct_args = (
                    "expose",
                    f"skillager-router/{digest}",
                    "--mode",
                    "stub",
                    "--agent",
                    "codex",
                    "--json",
                )
                router_args = (
                    "expose",
                    "project/member",
                    "--mode",
                    "router",
                    "--agent",
                    "codex",
                    "--json",
                )
                first = cli.run(*(router_args if router_first else direct_args))
                second = cli.run(*(direct_args if router_first else router_args), "--force")
                self.assert_code(first, 0)
                self.assert_code(second, 0)
                self.assertEqual(first.json()[0]["status"], "exposed")
                self.assertEqual(second.json()[0]["status"], "exposed")

                base = project / ".agents" / "skills" / f"skillager-router-{digest}"
                alternate = Path(second.json()[0]["target"])
                self.assertNotEqual(alternate, base)
                base_sidecar = load_mapping(base / "skillager.materialized.yaml")
                alternate_sidecar = load_mapping(alternate / "skillager.materialized.yaml")
                by_kind = {
                    base_sidecar["projection_kind"]: (base, base_sidecar),
                    alternate_sidecar["projection_kind"]: (alternate, alternate_sidecar),
                }
                self.assertEqual(set(by_kind), {"direct", "router-explicit"})
                router_target, router_sidecar = by_kind["router-explicit"]
                self.assertEqual(router_sidecar["router_kind"], "explicit")
                self.assertEqual(router_sidecar["router_slug"], router_target.name)
                self.assertIn(
                    f"--from-router {router_target.name}",
                    (router_target / "SKILL.md").read_text(encoding="utf-8"),
                )
                routed = cli.run(
                    "activate",
                    "project/member",
                    "--from-router",
                    router_target.name,
                    "--no-session-record",
                )
                self.assert_code(routed, 0)
                self.assertIn("Explicit router member", routed.stdout)

    @staticmethod
    def _write_skill(path: Path, body: str) -> None:
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            "---\n"
            f"name: {path.name}\n"
            f"description: Use {body.lower()}.\n"
            "---\n\n"
            f"# {path.name}\n\n{body}\n",
            encoding="utf-8",
        )
