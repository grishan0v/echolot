#!/usr/bin/env python3
"""The app's startup by Perfetto's own account — `_startup_info` and the header's lines.

What trace_processor's standard library makes of a launch is the self-check's
to pin, on the fixture's planted one: `startup: Perfetto's own account of the
launch, set against the window`. Here: which startup is described, what the
lines say, and how repeats merge.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import report as report_mod  # noqa: E402
from echolot.main import _startup_info  # noqa: E402
from tests.support import check  # noqa: E402

MS = 1_000_000
WINDOW = {"ts_start": 100 * MS, "ts_end": 1100 * MS}


class Session:
    """What `_startup_info` asks of a trace_processor, answered from a list."""

    def __init__(self, startups: list[dict], reasons: dict[int, list[dict]],
                 module: bool = True):
        self.startups, self.reasons, self.module, self.asked = startups, reasons, module, []

    def exec_script(self, sql: str) -> None:
        if not self.module:
            raise RuntimeError("Module not found: android.startup.startup_breakdowns")

    def query(self, sql: str) -> list[dict]:
        self.asked.append(sql)
        if "FROM android_startups" in sql:
            return self.startups
        startup_id = int(sql.split("startup_id = ", 1)[1].split()[0])
        return self.reasons.get(startup_id, [])


def startup(sid: int, start_ms: float, end_ms: float, kind: str = "cold") -> dict:
    return {"startup_id": sid, "ts": int(start_ms * MS), "ts_end": int(end_ms * MS),
            "dur": int((end_ms - start_ms) * MS), "type": kind}


def test_the_startup_that_shares_the_most_with_the_window_is_described() -> None:
    tp = Session([startup(1, 0, 90), startup(2, 40, 1105), startup(3, 1200, 1500, "hot")],
                 {2: [{"reason": "bind_application", "ns": 98 * MS},
                      {"reason": "Running", "ns": 967 * MS}]})
    found = _startup_info(tp, WINDOW, "com.example.app:remote")
    check("the package, not the process's name after the colon",
          "package = 'com.example.app'" in tp.asked[0], tp.asked[0])
    check("the second, which overlaps the window by 1000 ms",
          found["type"] == "cold" and found["dur_ms"] == 1065.0, found)
    check("set against the window, negative for before",
          (found["from_window_start_ms"], found["from_window_end_ms"], found["in_window_ms"])
          == (-60.0, 5.0, 1000.0), found)
    check("its reasons in milliseconds", found["reasons"]
          == {"bind_application": 98.0, "Running": 967.0}, found["reasons"])
    check("and the others counted", found["startups"] == 3, found)


def test_no_startup_of_the_app_or_no_module_is_no_line() -> None:
    check("a trace without a launch of this package",
          _startup_info(Session([], {}), WINDOW, "com.example.app") is None)
    said = io.StringIO()
    with contextlib.redirect_stderr(said):
        found = _startup_info(Session([], {}, module=False), WINDOW, "com.example.app")
    check("a trace_processor without the module: nothing, and stderr says why",
          found is None and "[!] startup:" in said.getvalue(), said.getvalue())


# --- the lines ---------------------------------------------------------------

BASE = {"type": "cold", "dur_ms": 1000.0, "from_window_start_ms": 0.0,
        "from_window_end_ms": 0.0, "in_window_ms": 1000.0}


def test_a_startup_reads_like_the_budget_with_its_states_in_words() -> None:
    lines = report_mod._startup_lines({**BASE, "reasons": {
        "Running": 500.0, "bind_application": 300.0, "R": 60.0, "R+": 40.0,
        "S": 95.0, "launch_delay": 5.0}})
    check("one line when the startup is the window", len(lines) == 1, lines)
    check("states in words, R and R+ as one, the library's names as code, "
          "and a reason under a percent left to the json",
          lines[0] == "Startup: cold, **1000 ms** from the launch to the first frame: "
                      "50% on a CPU · 30% `bind_application` · 10% waiting for a CPU · "
                      "10% sleeping", lines[0])
    check("no startup, no line", report_mod._startup_lines(None) == [])


def test_a_startup_that_is_not_the_window_says_so() -> None:
    lines = report_mod._startup_lines({**BASE, "reasons": {"Running": 1000.0},
                                       "from_window_start_ms": -127.4,
                                       "from_window_end_ms": 12.0})
    check("where it began and ended against the window",
          lines[1] == "That is the platform's measure rather than the window: the "
                      "startup began 127 ms before the window opened, and ended 12 ms "
                      "after it closed.", lines)
    apart = report_mod._startup_lines({**BASE, "reasons": {}, "in_window_ms": 0.0,
                                       "from_window_start_ms": -3000.0})
    check("and warns when the two do not meet at all", apart[1].startswith("> ⚠️"), apart)
    several = report_mod._startup_lines({**BASE, "reasons": {}, "startups": 3, "type": None})
    check("several in the trace, and a type Perfetto could not tell",
          "of a kind Perfetto could not tell" in several[0]
          and "the one of 3 in the trace" in several[0], several)


# --- repeats ------------------------------------------------------------------

def test_repeats_merge_reason_by_reason_and_keep_both_types_apart() -> None:
    def one(kind: str, bind: float, io_ms: float | None) -> dict:
        reasons = {"bind_application": bind}
        if io_ms is not None:
            reasons["io"] = io_ms
        return {"window": {"startup": {**BASE, "type": kind, "dur_ms": bind + (io_ms or 0),
                                       "reasons": reasons}}}
    merged = report_mod._merge_startup([one("cold", 100.0, 50.0), one("cold", 120.0, None),
                                        one("cold", 110.0, 10.0), {"window": {}}])
    check("the median per reason, a reason a repeat lacked counted as nothing",
          merged["reasons"] == {"bind_application": 110.0, "io": 10.0}, merged)
    check("and how many repeats had a startup", merged["runs"] == "3/4", merged)
    mixed = report_mod._merge_startup([one("cold", 100.0, None), one("warm", 50.0, None)])
    check("a set of cold and warm starts says both", mixed["type"] == "cold and warm", mixed)
    check("nothing to merge, nothing", report_mod._merge_startup([{"window": {}}]) is None)
