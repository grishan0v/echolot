#!/usr/bin/env python3
"""Claude Code as `echolot ui` starts it, played by a script, for the tests.

It takes the command line the page builds (`echolot/dig.py`), refuses any
other, and does what it is told by a plan (`FAKE_CLAUDE`, as JSON, where it
runs as a process; handed in directly where a test runs it in its own
process): records rounds into the open investigation as `collect` and
`analyze` would, closes it with a verdict or with words, ends in an error,
asks a question nobody answers, puts markers into the code, leaves a child
process running, or waits until it is told to end. At the end it prints one
JSON object, as `claude -p --output-format json` does.

Nothing here talks to a model or the network, and nothing is started but
the child it is told to leave.

    "do": "verdict" | "rounds" | "text" | "fail" | "budget" | "ask" | "hang" | "nothing"
    "rounds": how many rounds it records first (2)
    "handed": how many of those rounds read the recordings it was handed,
              filed with no `collect` in the investigation (0)
    "markers": put a temporary marker into this file first
    "child_pipe": a pipe a child it leaves running says its pid into, and
                  holds open while it runs
    "seen": a file it writes what it was handed into
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERDICT = ".echolot/verdict.json"
WHERE = "app/src/main/java/com/example/app/Store.kt:12"
CHILD = ("import os, sys, time\npipe = open(sys.argv[1], 'w')\npipe.write(str(os.getpid()) + '\\n')\npipe.flush()\n"
         "os.write(int(sys.argv[2]), b'1')\nos.close(int(sys.argv[2]))\nwhile True:\n    time.sleep(1)\n")


class Refused(SystemExit):
    pass


def parse(args: list[str]) -> argparse.Namespace:
    """The command line, as the page builds it. Anything else is refused the
    way a command line refuses what it does not take: exit 2."""
    p = argparse.ArgumentParser(prog="claude", add_help=False, exit_on_error=False)
    p.add_argument("-p", "--print", dest="request", required=True)
    p.add_argument("--output-format", choices=["json"], required=True)
    p.add_argument("--permission-mode", choices=["dontAsk"], required=True)
    p.add_argument("--permission-prompts", choices=["none"], required=True)
    p.add_argument("--setting-sources", required=True)
    p.add_argument("--max-budget-usd", type=float, required=True)
    p.add_argument("--allowedTools", nargs="+", required=True)
    try:
        ns, rest = p.parse_known_args(args)
    except (argparse.ArgumentError, SystemExit) as e:
        raise Refused(2) from e
    if rest:
        raise Refused(2)
    return ns


def _report(n: int) -> dict[str, Any]:
    from tests.test_report_page import known
    data = known()
    data["window"]["duration_ms"] = 1920.0 - 100 * (n - 1)
    return data


def _round(project: Path, n: int, recorded: bool = True) -> None:
    """One round as an agent's `collect` and `analyze` leave it: a set of
    traces filed, a report written and filed, the record touched. Not
    `recorded`, it is a round of the recordings the agent was handed:
    `analyze` alone."""
    from echolot import hunt
    out = project / ".echolot" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(_report(n)), encoding="utf-8")
    if recorded:
        hunt.touch(project, collect=True)
    hunt.record_report(project, out)
    hunt.touch(project, analyze=True)


def _echolot(project: Path, *args: str) -> int:
    """An echolot command, as the agent would run it in the project."""
    from echolot import main, recorder
    said = io.StringIO()
    here = Path.cwd()
    os.chdir(project)
    try:
        with recorder.isolated(), contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            return main.main(list(args))
    finally:
        os.chdir(here)


def _mark(project: Path, rel: str) -> None:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('package com.example.app\n\nfun fill() {\n'
                    '    android.os.Trace.beginSection("AGENTTMP_fill") // echolot:mark\n'
                    '    try { // echolot:mark\n        work()\n'
                    '    } finally { android.os.Trace.endSection() } // echolot:mark\n}\n', encoding="utf-8")


def _said(out: Any, **fields: Any) -> None:
    result = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 12, "duration_ms": 61000,
              "total_cost_usd": 1.23, "permission_denials": [], "result": "", **fields}
    out.write((json.dumps(result) + "\n").encode("utf-8"))
    out.flush()


def prepare(project: Path, plan: dict[str, Any]) -> None:
    """The work before the end: markers into the code, then the rounds."""
    if plan.get("markers"):
        _mark(project, plan["markers"])
    for n in range(1, int(plan.get("rounds", 2)) + 1):
        _round(project, n, recorded=n > int(plan.get("handed", 0)))


def act(ns: argparse.Namespace, project: Path, plan: dict[str, Any], out: Any) -> int:
    """What the plan says, then the one JSON object at the end. The exit code."""
    seen = plan.get("seen")
    if seen:
        Path(seen).write_text(json.dumps({"request": ns.request, "rules": ns.allowedTools,
                                          "sources": ns.setting_sources, "budget": ns.max_budget_usd,
                                          "env": {k: os.environ.get(k) for k in ("CLAUDECODE",)}}),
                              encoding="utf-8")
    do = plan.get("do", "verdict")
    if do == "fail":
        _said(out, subtype="error_during_execution", is_error=True, total_cost_usd=0.0,
              result="Failed to authenticate: OAuth session expired and could not be refreshed")
        return 1
    prepare(project, plan)
    if do == "budget":
        # Where `--max-budget-usd` stops it, as the Agent SDK's reference gives
        # the result: an error with that subtype, no last message, a cost its
        # own estimate puts past the cap. The exit code is not documented.
        _said(out, subtype="error_max_budget_usd", is_error=True, total_cost_usd=10.42, result=None,
              terminal_reason="budget_exhausted", errors=["Reached the maximum budget ($10.00)"])
        return int(plan.get("code", 1))
    if plan.get("child_pipe"):
        # A child left running, as a build or a recording would be: it says
        # its pid into a pipe and holds the pipe open for as long as it runs.
        # It goes on only once the child holds the pipe: ended before that,
        # the child would leave its reader waiting for a writer for ever.
        held, holding = os.pipe()
        subprocess.Popen([sys.executable, "-c", CHILD, plan["child_pipe"], str(holding)], pass_fds=(holding,),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(holding)
        os.read(held, 1)
        os.close(held)
    if do == "hang":
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 130
    if do == "ask":
        _said(out, result="I would have asked which build to measure, and nobody could answer, so I stopped here.",
              permission_denials=[{"tool_name": "AskUserQuestion", "tool_use_id": "t1",
                                   "tool_input": {"question": "Which build should I measure?"}},
                                  {"tool_name": "Bash", "tool_use_id": "t2", "tool_input": {"command": "git stash"}}])
        return 0
    if do == "text":
        _echolot(project, "hunt", "--done", "Store.fill is the slow part; confidence low")
        _said(out, result="Closed with a line.")
        return 0
    if do == "verdict":
        verdict = {"summary": "The launch waits for the store to be read.",
                   "causes": [{"title": "The store is read on the main thread", "where": [WHERE],
                               "evidence": [{"report": 1, "detector": "main_thread_waits", "location": "worker_pool_#"}],
                               "cost_ms": 700, "cost_of": "the main thread waiting", "fix": "Read it in the background.",
                               "confidence": "medium"}],
                   "cleanup": "removed"}
        (project / VERDICT).write_text(json.dumps(verdict), encoding="utf-8")
        code = _echolot(project, "hunt", "--done", "--verdict", VERDICT)
        _said(out, result="Done: the verdict is recorded." if code == 0 else f"hunt --done said {code}",
              permission_denials=plan.get("denied") or [])
        return 0
    _said(out, result="I looked at the report and the code, and have nothing to add.")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        ns = parse(sys.argv[1:] if argv is None else argv)
    except Refused as e:
        print("error: that is not the command line echolot ui builds", file=sys.stderr)
        return int(e.code or 2)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    plan = json.loads(os.environ.get("FAKE_CLAUDE") or "{}")
    return act(ns, Path.cwd(), plan, sys.stdout.buffer)


# ---------------------------------------------------------------------------
# for a test that runs the job in its own process: the same, as `Popen`


class Process:
    """What `subprocess.Popen` gives the job: this script's work done at once,
    or, for a plan that waits, a process that runs until it is signalled.
    `on_wait` is called each time the job looks while it runs."""

    def __init__(self, code: int, pid: int, waits: bool = False, on_wait=None):
        self.returncode, self.pid, self.code, self.waits, self.on_wait = None, pid, code, waits, on_wait
        self.signals: list[int] = []
        self.killed = False

    def wait(self, timeout=None):
        if self.killed:
            self.returncode = -9
            return self.returncode
        if self.waits and not self.signals:
            if self.on_wait:
                self.on_wait(self)
            raise subprocess.TimeoutExpired("claude", timeout)
        self.returncode = (-signal.SIGINT if self.signals else self.code)
        return self.returncode

    def send_signal(self, signum):
        self.signals.append(signum)

    def terminate(self):
        self.signals.append(signal.SIGTERM)

    def kill(self):
        self.killed = True


class Claude:
    """`subprocess.Popen` for the job, with this script in place of Claude
    Code: it checks the program and the command line, and does the plan in
    this process. `on_wait` is for a plan that waits ("hang")."""

    def __init__(self, program: str, plan: dict[str, Any] | None = None, on_wait=None):
        self.program, self.plan, self.on_wait = program, plan or {}, on_wait
        self.argv: list[str] | None = None
        self.env: dict[str, str] | None = None

    def __call__(self, argv, **kw):
        assert argv[0] == self.program, f"the job started {argv[0]}, not the client"
        self.argv, self.env = list(argv), dict(kw["env"])
        ns = parse(argv[1:])
        if self.plan.get("do") == "hang":
            # It works, and goes on working until it is told to end: a
            # stopped Claude Code prints no result.
            prepare(Path(kw["cwd"]), self.plan)
            return Process(0, pid=4900, waits=True, on_wait=self.on_wait)
        return Process(act(ns, Path(kw["cwd"]), self.plan, kw["stdout"]), pid=4900)


if __name__ == "__main__":
    raise SystemExit(main())
