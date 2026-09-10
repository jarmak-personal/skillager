from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.behavior.support import make_basic_workspace


@unittest.skipUnless(shutil.which("git"), "system Git is required")
class SqliteLibraryBehaviorTests(unittest.TestCase):
    def test_versions_bind_exact_git_objects_and_failed_approval_can_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, cli = make_basic_workspace(root)
            library = root / "library"
            for args in (("library", "init", "--path", str(library)), ("library", "new", "citrus")):
                result = cli.run(*args)
                self.assertEqual(result.code, 0, result.stderr)
            accepted = cli.run_confirmed("library", "accept", "citrus", "--yes", "--json")
            self.assertEqual(accepted.code, 0, accepted.stderr)
            first_hash = accepted.json()["skill"]["working_hash"]
            db = root / "state" / "catalog" / "trust.sqlite3"
            with closing(sqlite3.connect(db)) as conn:
                source_key, digest, commit, path = conn.execute("SELECT source_key, content_hash, git_commit, skill_path FROM version_references").fetchone()
                self.assertEqual(digest, first_hash)
                self.assertEqual(commit, accepted.json()["commit"]["commit"])
                self.assertEqual(path, "skills/citrus")
                event_version = json.loads(conn.execute("SELECT version_reference FROM decision_events ORDER BY sequence DESC LIMIT 1").fetchone()[0])
                self.assertEqual(event_version["git_commit"], commit)
                conn.execute("CREATE TRIGGER simulate_disk_failure BEFORE INSERT ON decision_events BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END")
                conn.commit()
            body = library / "skills" / "citrus" / "SKILL.md"
            body.write_text(body.read_text() + "\nUpdated citrus instructions for orchards.\n")
            failed = cli.run_confirmed("library", "accept", "citrus", "--yes", "--json")
            self.assertEqual(failed.code, 2, failed.stdout + failed.stderr)
            self.assertIn("simulated storage failure", failed.stderr)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=library, env=cli.env, capture_output=True, text=True, check=True).stdout.strip()
            self.assertNotEqual(head, commit)
            pending = cli.run("library", "status", "citrus", "--json")
            self.assertEqual(pending.code, 0, pending.stderr)
            self.assertNotEqual(pending.json()["skill"]["acceptance"], "accepted")
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute("SELECT content_hash FROM approvals WHERE key = ?", (source_key,)).fetchone()[0], first_hash)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM content_versions").fetchone()[0], 1)
                conn.execute("DROP TRIGGER simulate_disk_failure")
                conn.commit()
            retried = cli.run_confirmed("library", "accept", "citrus", "--yes", "--json")
            self.assertEqual(retried.code, 0, retried.stderr)
            self.assertIsNone(retried.json()["commit"])
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM content_versions").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT git_commit FROM version_references WHERE content_hash = ?", (retried.json()["skill"]["working_hash"],)).fetchone()[0], head)
            # Reaccepting at an unrelated HEAD records a reference, not a content version.
            (library / "notes.txt").write_text("Unrelated library note.\n")
            subprocess.run(["git", "add", "notes.txt"], cwd=library, env=cli.env, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "Unrelated note"], cwd=library, env=cli.env, check=True, capture_output=True)
            again = cli.run_confirmed("library", "accept", "citrus", "--yes", "--json")
            self.assertEqual(again.code, 0, again.stderr)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM content_versions").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM version_references").fetchone()[0], 3)
