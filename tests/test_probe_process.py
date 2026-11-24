"""`probe` on a trace where the mask matches more than the app.

A macrobenchmark's runner is a process of its own, `<package>.benchmark`,
and the mask `scan` writes, `<package>*`, matches it too. `probe` listed its
threads as the app's and led the anchor candidates with its `measureBlock`
(#727). It now takes one process, as `analyze` does.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402

APP, APP_PID = "com.example.app", 4100
RUNNER, RUNNER_PID, RUNNER_TID = "com.example.app.benchmark", 5100, 5101

# The app: a launch with two children, the time a thread spends in a slice
# being the parent's 100 ms and not 180.
APP_SLICES = [
    ("AppStart", 10, 100, [
        ("inflate", 20, 40, []),
        ("bindViews", 65, 40, []),
    ]),
]

# The runner: fewer slices than the app, and longer, as on a real benchmark.
RUNNER_SLICES = [
    ("measureBlock", 0, 900, [
        ("startActivityAndWait", 5, 300, []),
    ]),
]


def _prints(pid: int, tid: int, slices) -> list:
    events = fixture._flatten(slices, [], iter(range(10**6)))
    return [(ts, order, "print", tid, f"B|{pid}|{name}\n" if kind == "B" else f"E|{pid}\n")
            for ts, order, kind, name in events]


def build(app_slices=APP_SLICES, runner_slices=RUNNER_SLICES) -> bytes:
    builder = fixture.TraceProtoBuilder()
    fixture.process_tree(builder, [
        (APP_PID, APP, {APP_PID: APP}),
        (RUNNER_PID, RUNNER, {RUNNER_PID: RUNNER, RUNNER_TID: "roidJUnitRunner"}),
    ])
    fixture.ftrace(builder, {0: _prints(APP_PID, APP_PID, app_slices),
                             1: _prints(RUNNER_PID, RUNNER_TID, runner_slices)})
    return builder.serialize()


@pytest.fixture(scope="module")
def trace(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("probe") / "benchmark.perfetto-trace"
    path.write_bytes(build())
    return path


def probe(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["probe", *argv])
    return code, out.getvalue(), err.getvalue()


def section(text: str, title: str) -> str:
    return text.split(title, 1)[1].split("\n## ", 1)[0]


def test_a_mask_that_matches_the_runner_too_reads_the_app_alone(trace):
    code, out, err = probe(str(trace), "--process", "com.example.app*")
    check("exit 0", code == 0, (code, err))
    threads = section(out, "## Threads of process")
    check("the threads are the app's", APP in threads.splitlines()[0]
          and "roidJUnitRunner" not in threads, threads)
    anchors = section(out, "anchor candidates")
    check("and so are the anchor candidates", "AppStart" in anchors
          and "measureBlock" not in anchors and "startActivityAndWait" not in anchors,
          anchors)
    check("the process it took is the app, by its slices",
          "## Threads of process com.example.app (pid 4100)" in out, out)
    check("and the one it skipped is named on stderr",
          "matched 2 processes" in err and "Took com.example.app " in err
          and RUNNER in err, err)


def test_the_runner_can_still_be_read_by_its_own_name(trace):
    code, out, err = probe(str(trace), "--process", RUNNER)
    check("exit 0", code == 0, (code, err))
    check("its threads", "roidJUnitRunner" in section(out, "## Threads of process"), out)
    check("its slices", "measureBlock" in section(out, "anchor candidates"), out)
    check("and no word of other processes", "matched" not in err, err)
