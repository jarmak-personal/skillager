"""Fresh-process measurements for opt-in public CLI benchmarks."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from tests.behavior.support import CliResult, SkillagerCli


# Keep the measured process and RSS definition identical to benchmark_search.
MEASURE_CLI = """\
import json, runpy, sys
metrics_path = sys.argv.pop(1)
sys.argv[0] = 'skillager'
try:
    runpy.run_module('skillager', run_name='__main__')
finally:
    peak = None
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform != 'darwin':
            peak *= 1024
    except ImportError:
        pass
    with open(metrics_path, 'w', encoding='utf-8') as handle:
        json.dump({'peak_rss_bytes': peak}, handle)
"""


def measure_cli(cli: SkillagerCli, argv: tuple[str, ...] | list[str], metrics: Path) -> tuple[CliResult, dict]:
    metrics.unlink(missing_ok=True)
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-c", MEASURE_CLI, str(metrics), *argv],
        cwd=cli.project, env=cli.env, stdin=subprocess.DEVNULL,
        capture_output=True, timeout=cli.timeout, check=False,
    )
    sample = {
        "seconds": time.perf_counter() - started,
        "stdout_bytes": len(completed.stdout), "stderr_bytes": len(completed.stderr),
        **json.loads(metrics.read_text(encoding="utf-8")),
    }
    return CliResult(completed.returncode, completed.stdout.decode("utf-8"), completed.stderr.decode("utf-8")), sample
