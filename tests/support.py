from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from io import StringIO
from pathlib import Path


_TEST_TMP = Path(os.environ.get("SKILLAGER_TEST_TMPDIR", Path.home() / ".cache" / "skillager-test-tmp"))
_TEST_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_TEST_TMP)


class TtyStringIO(StringIO):
    def isatty(self) -> bool:
        return True


@contextmanager
def chdir(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
