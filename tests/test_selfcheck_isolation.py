"""The self-check leaves the process as it found it, and checks the binary it names.

`doctor` runs every check in its own process, and so does `init`. A check that
leaves something set changes what the command does after it; a trace a check
opens on another trace_processor than the one `--tp-binary` named vouches for
a binary nobody started.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import selftest, tp  # noqa: E402
from echolot.config import ConfigError  # noqa: E402
from tests.support import check  # noqa: E402

RUN_LOG_CHECK = "collect: the run log keeps the sentence a failure printed"
SANDBOX_CHECK = "doctor: a check the sandbox does not let run is a skip, and only that one"
SANDBOX_MARKS = ("CURSOR_SANDBOX", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED")


def test_a_check_leaves_the_run_log_on(monkeypatch) -> None:
    """A shell normally has no ECHOLOT_NO_RECORD, and after this check the
    doctor process had one: none of its runs reached the run log again."""
    monkeypatch.delenv("ECHOLOT_NO_RECORD", raising=False)
    results = selftest.run_checks({}, [c for c in selftest.CHECKS if c[0] == RUN_LOG_CHECK])
    check("the check passed", results == [(RUN_LOG_CHECK, None)], results)
    check("and left the variable unset", "ECHOLOT_NO_RECORD" not in os.environ,
          os.environ.get("ECHOLOT_NO_RECORD"))

    monkeypatch.setenv("ECHOLOT_NO_RECORD", "yes")
    selftest.run_checks({}, [c for c in selftest.CHECKS if c[0] == RUN_LOG_CHECK])
    check("or as it was", os.environ.get("ECHOLOT_NO_RECORD") == "yes")


def test_the_sandbox_check_holds_whichever_agent_runs_doctor(monkeypatch) -> None:
    """The check stands in for Cursor's sandbox. Run by Codex, doctor carries
    Codex's marks, which `sandbox.host()` reads first: the check failed, and
    doctor said no report made there could be trusted (#700)."""
    for env in ({}, {"CODEX_SANDBOX_NETWORK_DISABLED": "1"}, {"CODEX_SANDBOX": "seatbelt"},
                {"CURSOR_SANDBOX": "seatbelt"}):
        for name in SANDBOX_MARKS:
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        results = selftest.run_checks({}, [c for c in selftest.CHECKS if c[0] == SANDBOX_CHECK])
        check(f"passes with {env or 'no marks'}", results == [(SANDBOX_CHECK, None)], results)
        marks = {name: os.environ.get(name) for name in SANDBOX_MARKS}
        check("and leaves the marks as they were", marks == {name: env.get(name) for name in SANDBOX_MARKS},
              marks)


def test_every_trace_the_self_check_opens_is_on_the_binary_it_checks(
        marker_report, monkeypatch) -> None:
    """Each session that gets as far as starting trace_processor is asked for
    the run's binary. The sessions stop there: which binary was asked for is
    the whole question, and running every check for real is `doctor`'s."""
    asked: list[str | None] = []

    def opened(self, trace_path, binary=None, extra=None):
        if not Path(trace_path).is_file():
            raise ConfigError(f"no such trace: {trace_path}")
        asked.append(binary)
        raise ConfigError("stopped by the test")

    monkeypatch.setattr(tp.TraceSession, "__init__", opened)
    monkeypatch.setattr(selftest, "_binary", "/opt/perfetto/trace_processor_shell")
    selftest._sampled.cache_clear()
    try:
        selftest.run_checks(json.loads(json.dumps(marker_report)), selftest.CHECKS)
    finally:
        selftest._sampled.cache_clear()
    check("ten sessions or more were opened", len(asked) >= 10, asked)
    check("every one on the binary under check",
          set(asked) == {"/opt/perfetto/trace_processor_shell"}, asked)


def test_a_failure_is_placed_at_the_check_s_own_line() -> None:
    def broken(report):
        json.loads("{ not json")

    [(_, why)] = selftest.run_checks({}, [("decodes", broken)])
    check("a line of selftest.py first, where it was raised beside it",
          " at selftest.py:" in why and "(raised in decoder.py:" in why, why)
