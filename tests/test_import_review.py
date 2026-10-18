from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

from skillager.cli import main
from skillager.commands import importing as command
from skillager.library import service
from tests.behavior.support import CliResult
from tests.behavior import test_import_review as behavior


class ImportReviewBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = behavior.ImportReviewBehaviorTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.catalog = self.root / "state/catalog"
        self.default = self.root / "home/.skillager/library"
        environment = patch.dict(os.environ, self.fixture.cli.env)
        environment.start()
        self.addCleanup(environment.stop)
        previous = os.getcwd()
        os.chdir(self.fixture.project)
        self.addCleanup(os.chdir, previous)

    def confirm(self, preview):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(preview["next_command_argv"][1:])
        return CliResult(code, stdout.getvalue(), stderr.getvalue())

    @unittest.skipUnless(shutil.which("git"), "system Git is required")
    def test_first_use_cannot_rebind_a_library_that_appears_after_fresh_preview(self):
        preview = self.fixture.checked(self.fixture.preview())
        original = service.initialize_library
        after_other = None

        def another_initialization(*args, **kwargs):
            nonlocal after_other
            # A second cooperating operation wins the library lock first.
            original(self.catalog, path=self.default, no_git=True)
            after_other = self.fixture.state()
            return original(*args, **kwargs)

        with patch.object(command, "initialize_library", another_initialization):
            result = self.confirm(preview)
        self.fixture.checked(result, 2)
        self.assertEqual(self.fixture.state(), after_other)
        self.assertFalse((self.default / "skills/reviewable").exists())

    @unittest.skipUnless(shutil.which("git"), "system Git is required")
    def test_first_use_cannot_register_an_identity_that_appears_after_fresh_preview(self):
        preview = self.fixture.checked(self.fixture.preview())
        original = service.initialize_library
        after_other = None

        def another_identity(*args, **kwargs):
            nonlocal after_other
            original(self.root / "foreign-catalog", path=self.default, no_git=True)
            after_other = self.fixture.state()
            return original(*args, **kwargs)

        with patch.object(command, "initialize_library", another_identity):
            result = self.confirm(preview)
        self.fixture.checked(result, 2)
        # Acquiring a lock can create the private lock file, but no library
        # registration or approval is allowed after the expected context changed.
        after = self.fixture.state()
        self.assertEqual({key: value for key, value in after.items() if ".skillager-locks" not in key}, after_other)
        self.assertFalse((self.default / "skills/reviewable").exists())

    def test_preexisting_reviewed_identity_is_not_replaced_during_registration(self):
        service.initialize_library(self.root / "foreign-catalog", path=self.default, no_git=True)
        preview = self.fixture.checked(self.fixture.preview())
        original = service.initialize_library
        after_other = None

        def replaced_identity(*args, **kwargs):
            nonlocal after_other
            identity_path = self.default / ".skillager/library.json"
            identity = json.loads(identity_path.read_text())
            identity["library_id"] = str(uuid4())
            identity_path.write_text(json.dumps(identity))
            after_other = self.fixture.state()
            return original(*args, **kwargs)

        with patch.object(command, "initialize_library", replaced_identity):
            result = self.confirm(preview)
        self.fixture.checked(result, 2)
        after = self.fixture.state()
        self.assertEqual({key: value for key, value in after.items() if ".skillager-locks" not in key}, after_other)
        self.assertFalse((self.default / "skills/reviewable").exists())

    @unittest.skipUnless(shutil.which("git"), "system Git is required")
    def test_git_mode_is_revalidated_under_the_import_lock(self):
        self.fixture.initialize(git=True)
        preview = self.fixture.checked(self.fixture.preview())
        original = command.import_library_skill
        after_other = None

        def changed_mode(*args, **kwargs):
            nonlocal after_other
            identity_path = self.fixture.library / ".skillager/library.json"
            identity = json.loads(identity_path.read_text())
            identity["git"]["mode"] = "disabled"
            identity_path.write_text(json.dumps(identity))
            after_other = self.fixture.state()
            return original(*args, **kwargs)

        with patch.object(command, "import_library_skill", changed_mode):
            result = self.confirm(preview)
        self.fixture.checked(result, 2)
        after = self.fixture.state()
        self.assertEqual({key: value for key, value in after.items() if ".skillager-locks" not in key},
                         {key: value for key, value in after_other.items() if ".skillager-locks" not in key})
        self.assertFalse((self.fixture.library / "skills/reviewable").exists())

    @unittest.skipUnless(shutil.which("git"), "system Git is required")
    def test_change_after_initialization_reports_completed_setup_without_import(self):
        preview = self.fixture.checked(self.fixture.preview())
        original = service.library_status

        def changed_after_setup(*args, **kwargs):
            identity_path = self.default / ".skillager/library.json"
            identity = json.loads(identity_path.read_text())
            identity["git"]["mode"] = "disabled"
            identity_path.write_text(json.dumps(identity))
            return original(*args, **kwargs)

        with patch.object(service, "library_status", changed_after_setup):
            result = self.confirm(preview)
        refused = self.fixture.checked(result, 2)
        self.assertEqual(refused["error"]["code"], "review_changed")
        self.assertIn("setup completed", refused["error"]["message"])
        self.assertTrue((self.default / ".skillager/library.json").exists())
        self.assertFalse((self.default / "skills/reviewable").exists())
