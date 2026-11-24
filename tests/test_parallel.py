"""The traces of a set, read side by side (#488).

`analyze` reads each trace in a trace_processor of its own, and now several
at once. What the run looks like has to stay what it was one trace at a time:
the reports merged in the order the traces were given, each trace's lines
printed whole and in that order, the first failure in that order ending the
run, and no trace_processor left behind however the run ends.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from echolot import fixture, orphans, parallel, recorder, tp  # noqa: E402
from echolot.config import ConfigError  # noqa: E402
from echolot.main import main, run_order  # noqa: E402
from tests.support import check  # noqa: E402

CONFIG = """\
project:
  package: com.example.app
  process: com.example.app
scenario:
  name: fixture
  start: {name: AppStart}
  end: {name: Screen.firstFrame}
"""


# --- how many at once -------------------------------------------------------

def _sized(tmp_path: Path, *sizes: int) -> list[Path]:
    out = []
    for i, size in enumerate(sizes):
        p = tmp_path / f"{i}.perfetto-trace"
        with p.open("wb") as f:
            f.truncate(size)
        out.append(p)
    return out


def test_as_many_as_cores_traces_and_memory_allow(tmp_path: Path) -> None:
    traces = _sized(tmp_path, *[20 << 20] * 15)
    each = parallel.MEMORY_PER_BYTE * (20 << 20) + parallel.MEMORY_FLOOR
    check("the cores, when memory is plenty",
          parallel.jobs_for(traces, cpus=11, ram=64 << 30) == 11)
    check("the traces, when there are fewer of them than cores",
          parallel.jobs_for(traces[:3], cpus=11, ram=64 << 30) == 3)
    check("the memory, when half of it holds fewer of the largest trace",
          parallel.jobs_for(traces, cpus=11, ram=int(4 * each / parallel.MEMORY_SHARE)) == 4)
    (tmp_path / "big").mkdir()
    mixed = _sized(tmp_path / "big", *[20 << 20] * 7, 400 << 20)
    check("the largest trace sets the size of every worker: half of 32 GB holds "
          "three of a 400 MB trace's 4.8 GB, however small the rest",
          parallel.jobs_for(mixed, cpus=11, ram=32 << 30) == 3,
          parallel.jobs_for(mixed, cpus=11, ram=32 << 30))
    check("never fewer than one, however little memory",
          parallel.jobs_for(traces, cpus=11, ram=1 << 20) == 1)
    check("unknown memory leaves the cores and the traces",
          parallel.jobs_for(traces, cpus=6, ram=0) == 6)


def test_jobs_asked_for_win_but_not_over_the_traces(tmp_path: Path) -> None:
    traces = _sized(tmp_path, 1, 1, 1)
    check("-j 2", parallel.jobs_for(traces, 2, cpus=1, ram=1) == 2)
    check("-j 8 on three traces is three", parallel.jobs_for(traces, 8) == 3)
    check("-j 1", parallel.jobs_for(traces, 1, cpus=11, ram=64 << 30) == 1)


def test_jobs_must_be_one_or_more(tmp_path: Path, capsys) -> None:
    for bad in ("0", "-2", "many"):
        with pytest.raises(SystemExit) as e:
            main(["analyze", "-j", bad, "x.perfetto-trace"])
        check(f"-j {bad} is refused by the parser", e.value.code == 2, e.value.code)
    check("and says what to give", "give 1 or more" in capsys.readouterr().err)


# --- the order --------------------------------------------------------------

def test_results_and_lines_come_in_the_order_given() -> None:
    """The last item finishes first, the first last; nothing shows it."""
    def work(i: int) -> int:
        print(f"start {i}", file=sys.stderr)
        time.sleep(0.05 * (3 - i))
        print(f"out {i}")
        print(f"end {i}", file=sys.stderr)
        return i * 10

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        got = parallel.in_order([0, 1, 2, 3], work, 4)
    check("results in the order given", got == [0, 10, 20, 30], got)
    check("each item's lines whole, item after item",
          err.getvalue().splitlines() == [f"{w} {i}" for i in range(4) for w in ("start", "end")],
          err.getvalue())
    check("stdout too", out.getvalue().splitlines() == [f"out {i}" for i in range(4)], out.getvalue())


def test_the_streams_and_signals_are_given_back() -> None:
    out, err = sys.stdout, sys.stderr
    parallel.in_order([1, 2], lambda i: i, 2)
    check("sys.stdout and sys.stderr are the ones from before",
          sys.stdout is out and sys.stderr is err)
    check("and no signal is held after the run", orphans._held == 0, orphans._held)


def test_one_job_is_a_plain_loop_in_this_thread() -> None:
    seen = []
    parallel.in_order([1, 2, 3], lambda i: seen.append(threading.current_thread()), 1)
    check("every item in the calling thread",
          seen == [threading.current_thread()] * 3, seen)


def test_the_first_failure_in_the_order_given_ends_the_run() -> None:
    """Item 2 fails first in time; item 1's failure is the one raised, after
    item 0's lines and its own. Item 2's lines and item 3's never show."""
    def work(i: int) -> int:
        time.sleep({0: 0.05, 1: 0.15, 2: 0.0, 3: 0.3}[i])
        print(f"item {i}", file=sys.stderr)
        if i in (1, 2):
            raise ConfigError(f"item {i} failed")
        return i

    err = io.StringIO()
    with contextlib.redirect_stderr(err), pytest.raises(ConfigError) as e:
        parallel.in_order([0, 1, 2, 3], work, 4)
    check("the first in order", str(e.value) == "item 1 failed", e.value)
    check("after what came before it, and nothing after",
          err.getvalue().splitlines() == ["item 0", "item 1"], err.getvalue())


# --- analyze, end to end ----------------------------------------------------

def _set(root: Path) -> list[str]:
    variants = {"c": {}, "a": {"crowded": True}, "b": {"frames": False}}
    out = []
    for name, build in variants.items():
        p = root / f"{name}.perfetto-trace"
        p.write_bytes(fixture.build(**build))
        out.append(str(p))
    return out


def _analyze(root: Path, out: str, *argv: str) -> tuple[int, dict | None, str, str]:
    (root / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    so, se = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
        code = main(["analyze", "-c", str(root / "echolot.yml"), "-o", out, *argv])
    path = root / out / "report.json"
    rep = json.loads(path.read_text()) if code == 0 and path.exists() else None
    return code, rep, so.getvalue(), se.getvalue()


def test_j1_and_j3_write_the_same_report(tmp_path: Path) -> None:
    traces = _set(tmp_path)
    # --full: the whole report, which names no time; the overview a pipe
    # gets says when it was written (#492).
    c1, one, md1, _ = _analyze(tmp_path, "one", "-j", "1", "--full", *traces)
    c3, three, md3, _ = _analyze(tmp_path, "three", "-j", "3", "--full", *traces)
    check("both ran", c1 == c3 == 0, (c1, c3))
    one.pop("generated_at", None)
    three.pop("generated_at", None)
    # Named c, a, b: the report keeps them in one order whatever order they
    # were named in (#581), and -j 3 does not change it.
    check("the traces in the set's own order",
          three["traces"] == sorted(traces, key=run_order), three["traces"])
    check("report.json is the same, apart from when", one == three)
    check("and so is what was printed",
          md1.replace(str(tmp_path / "one"), "") == md3.replace(str(tmp_path / "three"), ""))


def test_a_warning_about_one_trace_comes_after_the_traces_before_it(
        tmp_path: Path, monkeypatch) -> None:
    """A detector fails on every trace, and the first trace is held back the
    longest. Its line still comes first."""
    traces = sorted(_set(tmp_path), key=run_order)   # the order analyze reads them in (#581)
    first = Path(traces[0]).resolve()
    real = tp.TraceSession.query

    def query(self, sql):
        if "@id: frame_jank" in sql:
            which = getattr(self, "_which", "?")
            if which == first.name:
                time.sleep(0.4)
            raise RuntimeError(f"planted in {which}")
        return real(self, sql)

    real_init = tp.TraceSession.__init__

    def init(self, trace_path, *a, **kw):
        self._which = Path(trace_path).name
        real_init(self, trace_path, *a, **kw)

    monkeypatch.setattr(tp.TraceSession, "query", query)
    monkeypatch.setattr(tp.TraceSession, "__init__", init)
    code, _, _, err = _analyze(tmp_path, "out", "-j", "3", *traces)
    check("the run went on", code == 0, err[-600:])
    planted = [ln for ln in err.splitlines() if "planted in" in ln]
    check("one line per trace, in the set's order",
          planted == [f"[!] frame_jank: planted in {Path(t).name}" for t in traces], planted)


def test_a_stopped_worker_ends_at_its_next_query(tmp_path: Path, monkeypatch) -> None:
    """Two traces are among their detectors when the third fails, and
    neither asks for another: `analyze_trace` reads every detector under an
    `except Exception`, and without `Stopped` each went on through all the
    rest of them, every query failing at once."""
    from echolot.config import Config
    from echolot.analyze import analyze_trace

    traces = _set(tmp_path)[:2]
    bad = tmp_path / "bad.perfetto-trace"
    bad.write_text("not a trace\n" * 64, encoding="utf-8")
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    cfg = Config.load(str(tmp_path / "echolot.yml"))
    in_detectors = threading.Semaphore(0)
    stopped = threading.Event()
    after: list[str] = []
    real_query, real_stop = tp.TraceSession.query, orphans.Group.stop

    def query(self, sql):
        if stopped.is_set():
            after.append(threading.current_thread().name)
        elif "@id: main_thread_block" in sql:
            in_detectors.release()
            stopped.wait(30)   # held here until the run is stopped
        return real_query(self, sql)

    def stop(self):
        stopped.set()
        real_stop(self)

    def read(trace):
        if trace == str(bad):
            for _ in traces:
                check("the others reached their detectors", in_detectors.acquire(timeout=30))
        return analyze_trace(trace, cfg)

    monkeypatch.setattr(tp.TraceSession, "query", query)
    monkeypatch.setattr(orphans.Group, "stop", stop)
    with contextlib.redirect_stderr(io.StringIO()), pytest.raises(ConfigError):
        parallel.in_order([str(bad), *traces], read, 3)
    check("the run was stopped while both were among their detectors", stopped.is_set())
    check("and neither asked for another detector after it", after == [], after)


def test_a_trace_that_cannot_be_read_is_the_runs_error(tmp_path: Path) -> None:
    traces = _set(tmp_path)
    bad = tmp_path / "bad.perfetto-trace"
    bad.write_text("not a trace\n" * 64, encoding="utf-8")
    code, _, _, err = _analyze(tmp_path, "out", "-j", "3", traces[0], str(bad), traces[1])
    check("exit 2", code == 2, code)
    check("naming that file", f"error: {bad.resolve()}" in err, err[-400:])


# --- however the run ends, no trace_processor stays -----------------------

pytestmark_posix = pytest.mark.skipif(os.name != "posix", reason="POSIX signals")

# Three traces read side by side, each worker querying until it is closed;
# each child's pid written once all three are open. Straight to the file
# descriptor: a worker's print is kept for its turn, which never comes here.
HOLDER = """
import os, sys, threading, time
from echolot import orphans, parallel
from echolot.tp import TraceSession
opened = []
lock = threading.Lock()
def read(path):
    with TraceSession(path) as tp:
        with lock:
            opened.append(orphans.pid_of(tp._tp))
            if len(opened) == 3:
                os.write(1, (" ".join(map(str, opened)) + chr(10)).encode())
        while True:
            tp.query("SELECT 1")
            time.sleep(0.01)
parallel.in_order(sys.argv[1:], read, 3)
"""


def _alive(pid: int) -> bool:
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                           capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _gone_within(pids: list[int], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not any(_alive(p) for p in pids):
            return True
        time.sleep(0.05)
    return not any(_alive(p) for p in pids)


@pytestmark_posix
@pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP", "SIGINT", "SIGKILL"])
def test_a_signal_during_a_parallel_run_ends_every_child(tmp_path: Path, signame: str) -> None:
    traces = _set(tmp_path)
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, *traces], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, cwd=ROOT, start_new_session=True,
        env=dict(os.environ, PYTHONPATH=str(ROOT), ECHOLOT_NO_RECORD="1"))
    pids: list[int] = []
    try:
        ready, _, _ = select.select([holder.stdout], [], [], 60)
        line = holder.stdout.readline().strip() if ready else ""
        check("three sessions opened", len(line.split()) == 3, line)
        pids = [int(p) for p in line.split()]
        check("their children are running", all(_alive(p) for p in pids), pids)
        sig = getattr(signal, signame)
        holder.send_signal(sig)
        code = holder.wait(timeout=30)
        if sig in (signal.SIGTERM, signal.SIGHUP):
            check("echolot exits with the signal's code", code == 128 + sig, code)
        check("and every child is gone", _gone_within(pids, 10), pids)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()
        for pid in pids:
            with contextlib.suppress(OSError):
                os.killpg(pid, signal.SIGKILL)
