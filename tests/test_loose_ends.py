#!/usr/bin/env python3
"""What the six changes of the 2026-09-28 audit left saying the old thing.

Each of them fixed its own ground and stopped at its edge, and past those
edges the tool went on printing what had stopped being true:

- `echolot` read a doctor that never ran its self-check — `checks: 0` and
  one entry in `failed`, as #118 records it — as "1 check(s) FAILED".
- `probe` opened a trace on the flag or the pin, while `analyze` from the
  same directory ran on the binary local.yml names.
- `hunt "<q>"` on a config that does not load opened an investigation with
  no scenario, and left the previous traces where the new ones would land,
  without a word. Since #121 one malformed entry under `detectors:` is a
  config that does not load.
- report.md for a run where nothing fired stopped before the Silent line and
  the toolchain footer — the footer being where #118 names a trace_processor
  that bypassed the pin.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path

import pytest

from echolot import hunt as hunt_mod
from echolot import main as main_mod
from echolot import recorder
from echolot import report as report_mod
from echolot.main import NOT_RUN, main
from tests.support import check

CUSTOM = "/opt/custom/trace_processor_shell"
CONFIG = """\
project:
  package: com.example.app
  process: com.example.app
scenario:
  name: coldStart
"""
# `true` is not a setting under a detector, so the whole config is refused
# on load — the shape #121 made fatal.
BROKEN = CONFIG + "detectors:\n  frame_jank: true\n"


def run(*argv: str) -> tuple[int, str, str]:
    """A command, with what it printed on each stream."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def project(monkeypatch, tmp_path) -> Path:
    """An empty project directory to run from, and the recorder put back after.

    `main` points the recorder at the project it runs in, and that global
    would otherwise outlive the test.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(recorder, "_root", None)
    return tmp_path


def _traces(root: Path, n: int = 3) -> Path:
    d = root / ".echolot" / "traces"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"coldStart_iter{i:03d}.perfetto-trace").write_bytes(b"x")
    return d


# --- status: a self-check that never ran has no tally -----------------------

def _doctor_line(root: Path, facts: dict) -> str:
    """The doctor line `echolot` prints over a run log holding one doctor."""
    log = root / recorder.LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({
        "ts": "2026-09-28T10:00:00+00:00", "cmd": "doctor",
        "argv": ["doctor", "-q"], "exit": 1, "facts": facts,
        "error": "self-check: could not run — could not download "
                 "trace_processor: offline",
    }) + "\n", encoding="utf-8")
    code, out, _ = run()
    assert code == 0, out
    return next(ln for ln in out.splitlines() if ln.startswith("doctor"))


def test_a_self_check_that_did_not_run_is_not_given_a_count(project):
    """What `_record_not_run` writes: `checks: 0` and the one `NOT_RUN` entry."""
    line = _doctor_line(project, {"checks": 0, "failed": [NOT_RUN]})
    check("says the self-check did not run", "the self-check did not run" in line, line)
    check("and points at doctor", "`echolot doctor`" in line, line)
    check("with no count of checks that never ran",
          "check(s)" not in line and "FAILED" not in line, line)
    check("and no pass", "passed" not in line, line)


@pytest.mark.parametrize("facts,said", [
    ({"checks": 143, "failed": ["a check", "another"]}, "2 check(s) FAILED"),
    ({"checks": 143, "failed": []}, "passed"),
], ids=["failed", "passed"])
def test_a_self_check_that_ran_keeps_its_tally(project, facts, said):
    line = _doctor_line(project, facts)
    check(f"the line says {said!r}", said in line, line)
    check("and not that it did not run", "did not run" not in line, line)

# --- probe: the binary analyze would use ------------------------------------

@pytest.fixture
def opened(monkeypatch) -> list:
    """The binary each trace was opened with, collected instead of run.

    Which binary `probe` was handed is the question here, not what that
    binary would have said about a trace.
    """
    seen: list = []

    class Session:
        def __init__(self, trace, binary=None):
            seen.append(binary)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def query(self, sql):
            return []

    monkeypatch.setattr(main_mod, "TraceSession", Session)
    return seen


def test_probe_opens_the_trace_with_the_binary_local_yml_names(project, opened):
    (project / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    (project / "local.yml").write_text(f"toolchain:\n  tp_binary: {CUSTOM}\n",
                                       encoding="utf-8")
    code, out, err = run("probe", "t.perfetto-trace")
    check("probe ran", code == 0, out + err)
    check("on the binary analyze would have used", opened == [CUSTOM], opened)


def test_probe_keeps_the_order_analyze_uses(project, opened):
    """The flag, then the config, then the pin."""
    (project / "local.yml").write_text(f"toolchain:\n  tp_binary: {CUSTOM}\n",
                                       encoding="utf-8")
    run("probe", "t.perfetto-trace")
    check("local.yml alone is not a config: the pin", opened == [None], opened)

    (project / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    run("probe", "t.perfetto-trace", "--tp-binary", "/opt/flag/tp")
    check("the flag outranks the config", opened[-1] == "/opt/flag/tp", opened)


def test_a_config_that_does_not_load_does_not_stop_probe(project, opened):
    (project / "echolot.yml").write_text(BROKEN, encoding="utf-8")
    (project / "local.yml").write_text(f"toolchain:\n  tp_binary: {CUSTOM}\n",
                                       encoding="utf-8")
    code, out, err = run("probe", "t.perfetto-trace")
    check("probe still ran", code == 0 and opened == [None], f"{opened}\n{err}")
    check("and said why the binary is the pinned one",
          "does not load" in err and "the pinned one" in err
          and "frame_jank" in err, err)

# --- hunt: a config that does not load opens nothing ------------------------

def test_hunt_refuses_a_config_that_does_not_load(project):
    (project / "echolot.yml").write_text(BROKEN, encoding="utf-8")
    traces = _traces(project)

    code, out, err = run("hunt", "cold start 3s → 7s")
    check("refused, exit 2", code == 2, f"exit {code}\n{out}{err}")
    check("with what failed", "echolot.yml does not load" in err
          and "detectors.frame_jank" in err, err)
    check("and what did not happen", "no traces were moved aside" in err, err)
    check("nothing was opened", hunt_mod.load(project) is None, hunt_mod.load(project))
    check("the traces are where they were",
          len(list(traces.glob("*.perfetto-trace"))) == 3,
          sorted(p.name for p in traces.iterdir()))


def test_the_investigation_already_open_is_left_as_it_was(project):
    """Opening closes the previous one as abandoned; a refusal must not."""
    (project / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    code, out, err = run("hunt", "the list stutters")
    assert code == 0, out + err
    before = hunt_mod.path(project).read_text(encoding="utf-8")

    (project / "echolot.yml").write_text(BROKEN, encoding="utf-8")
    code, _, err = run("hunt", "cold start 3s → 7s")
    check("refused", code == 2, err)
    check("the open investigation is untouched",
          hunt_mod.path(project).read_text(encoding="utf-8") == before)

    # Fixed, the same question opens, and the traces move aside as always.
    (project / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    traces = _traces(project)
    code, out, err = run("hunt", "cold start 3s → 7s")
    check("opens once the config loads", code == 0 and "opened #2" in out, out + err)
    check("and sets the old traces aside", "set aside: 3 trace(s)" in err, err)
    check("none left loose", not list(traces.glob("*.perfetto-trace")))


def test_the_run_log_keeps_the_refusal(project, monkeypatch):
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)
    (project / "echolot.yml").write_text(BROKEN, encoding="utf-8")
    run("hunt", "cold start 3s → 7s")
    hunts = [r for r in recorder.read(project / recorder.LOG_FILE)
             if r.get("cmd") == "hunt"]
    check("one hunt line", len(hunts) == 1, hunts)
    check("exit 2, with the sentence", hunts[0].get("exit") == 2
          and "does not load" in (hunts[0].get("error") or ""), hunts[0])

# --- report.md: nothing fired, and still the footer -------------------------

def _quiet(toolchain: dict) -> dict:
    """A report in which the one detector that ran found nothing."""
    return {
        "schema": 1, "generated_at": "2026-09-28T10:00:00+00:00",
        "trace": "t.perfetto-trace", "toolchain": toolchain,
        "window": {"process": "com.example.app", "duration_ms": 1000.0},
        "environment": {},
        "summary": {"detectors_run": 1, "detectors_fired": 0, "fired_ids": []},
        "detectors": [{"id": "d", "title": "d", "why": "", "params": {},
                       "params_source": "default", "error": None, "rows": []}],
    }


@pytest.mark.parametrize("toolchain,footer", [
    ({"trace_processor": "v48.0", "source": "toolchain.tp_binary", "binary": CUSTOM},
     "trace_processor v48.0 (custom binary from toolchain.tp_binary, pin bypassed)"),
    ({"trace_processor": "v56.1", "source": "pinned", "binary": None},
     "trace_processor v56.1"),
], ids=["custom", "pinned"])
def test_a_report_where_nothing_fired_keeps_silent_and_the_footer(toolchain, footer):
    lines = report_mod.to_markdown(_quiet(toolchain)).splitlines()
    check("it still says nothing fired", "_No detector fired._" in lines, lines)
    check("the Silent line is there", "**Silent:** d" in lines, lines)
    check("and the footer ends it", lines[-1] == f"<sub>{footer}</sub>", lines[-3:])


def test_the_fixture_with_every_row_emptied_names_each_detector_silent(marker_report):
    """The same, on the pipeline's own report: twelve names and a version."""
    quiet = copy.deepcopy(marker_report)
    for d in quiet["detectors"]:
        d["rows"] = []
    quiet["summary"].update(detectors_fired=0, fired_ids=[])

    lines = report_mod.to_markdown(quiet).splitlines()
    ids = [d["id"] for d in quiet["detectors"]]
    check("every detector named silent", f"**Silent:** {', '.join(ids)}" in lines,
          [ln for ln in lines if ln.startswith("**Silent")])
    version = marker_report["toolchain"]["trace_processor"]
    check("and the pinned trace_processor last",
          lines[-1] == f"<sub>trace_processor {version}</sub>", lines[-1])
