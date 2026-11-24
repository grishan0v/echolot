#!/usr/bin/env python3
"""Codex as `echolot ui` starts it, played by a script, for the tests.

It takes the command line the page builds (`echolot/dig_codex.py`), refuses
any other, and does what it is told by a plan (`FAKE_CODEX`, as JSON, where it
runs as a process; handed in directly where a test runs it in its own
process). It prints what `codex exec --json` prints, as the real one did in
the spike of #449: a line on stderr first, then one JSON object a line, items
started and completed, and last the turn with its token usage. Its exit code
is 0 even when its sandbox refused something, as the real one's is.

Nothing here talks to a model or the network, and nothing is started but the
child it is told to leave.

    "do": "verdict" | "rounds" | "refused" | "fail" | "ask" | "hang" | "nothing"
    "rounds": how many rounds it records first (2)
    "markers": put a temporary marker into this file first
    "outside": {"change": [...], "add": [...], "remove": [...]}, files it
               changes, adds or removes beside its work, wherever they are
    "child_pipe": a pipe a child it leaves running says its pid and its
                  process group into, and holds open while it runs; the child
                  is in a process group of its own, as Codex starts each
                  command it runs
    "seen": a file it writes what it was handed into
"""

from __future__ import annotations

import argparse
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

from echolot import hunt as hunt_mod  # noqa: E402
from tests import fake_claude  # noqa: E402

VERDICT = fake_claude.VERDICT
STDERR = "Reading additional input from stdin...\n"
USAGE = {"input_tokens": 182340, "cached_input_tokens": 150016, "cache_write_input_tokens": 0,
         "output_tokens": 4210, "reasoning_output_tokens": 1024}
QUESTION = "Which build should I measure, the debug one or the benchmark one?"
# A child in a process group of its own, as Codex starts the commands it
# runs: it says its pid and its group, then holds the pipe for as long as it
# runs.
CHILD = ("import os, sys, time\nos.setpgid(0, 0)\npipe = open(sys.argv[1], 'w')\n"
         "pipe.write(f'{os.getpid()} {os.getpgid(0)}\\n')\npipe.flush()\n"
         "os.write(int(sys.argv[2]), b'1')\nos.close(int(sys.argv[2]))\nwhile True:\n    time.sleep(1)\n")


class Refused(SystemExit):
    pass


def parse(args: list[str]) -> argparse.Namespace:
    """The command line, as the page builds it: `exec`, events as JSON Lines,
    the sandbox that writes inside the project, the project, the request.
    Anything else, a flag that bypasses approvals or the sandbox among it, is
    refused the way a command line refuses what it does not take: exit 2."""
    if not args or args[0] != "exec":
        raise Refused(2)
    p = argparse.ArgumentParser(prog="codex exec", add_help=False, exit_on_error=False)
    p.add_argument("--json", action="store_true")
    p.add_argument("-s", "--sandbox", choices=["workspace-write"], required=True)
    p.add_argument("-C", "--cd", required=True)
    p.add_argument("prompt")
    try:
        ns, rest = p.parse_known_args(args[1:])
    except (argparse.ArgumentError, SystemExit) as e:
        raise Refused(2) from e
    if rest or not ns.json:
        raise Refused(2)
    return ns


class Events:
    """What it prints, as Codex prints it: one JSON object a line."""

    def __init__(self, out: Any):
        self.out, self.n = out, 0

    def write(self, event: dict[str, Any]) -> None:
        self.out.write((json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8"))
        self.out.flush()

    def said(self, text: str) -> None:
        self.write({"type": "item.completed", "item": {"id": f"item_{self.n}", "type": "agent_message", "text": text}})
        self.n += 1

    def ran(self, command: str, output: str = "", code: int = 0) -> None:
        """A command it ran in its sandbox, wrapped in the shell it uses."""
        item = {"id": f"item_{self.n}", "type": "command_execution", "command": f"/bin/zsh -lc {command!r}",
                "aggregated_output": "", "exit_code": None, "status": "in_progress"}
        self.write({"type": "item.started", "item": item})
        self.write({"type": "item.completed", "item": {**item, "aggregated_output": output, "exit_code": code,
                                                       "status": "completed" if code == 0 else "failed"}})
        self.n += 1


def _change(project: Path, outside: dict[str, Any]) -> None:
    for rel in outside.get("change") or []:
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("// changed by the agent\n")
    for rel in outside.get("add") or []:
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text("a file the agent made\n", encoding="utf-8")
    for rel in outside.get("remove") or []:
        (project / rel).unlink()


def prepare(project: Path, plan: dict[str, Any], events: Events) -> None:
    """The work before the end: the thread and its turn, markers into the
    code and any other change it was told to make, then the rounds."""
    events.write({"type": "thread.started", "thread_id": "01a1f409-cb24-7470-8978-1801328c40fe"})
    events.write({"type": "turn.started"})
    events.said("I'll read `echolot guide hunt` first, then carry on in the investigation the page opened.")
    events.ran("echolot guide hunt", "# The hunt: from a report down to a place in the code\n")
    if plan.get("markers"):
        fake_claude._mark(project, plan["markers"])
    _change(project, plan.get("outside") or {})
    # Each round filed under the open investigation, as the request says.
    under = (hunt_mod.load(project) or {}).get("n")
    for n in range(1, int(plan.get("rounds", 2)) + 1):
        events.ran(f"echolot collect -c echolot.yml -n 3 --hunt {under}", "3 launches recorded\n")
        fake_claude._round(project, n)
        events.ran(f"echolot analyze .echolot/traces -c echolot.yml --hunt {under}", "report written\n")


def _leave_a_child(pipe: str) -> None:
    """A child left running in a group of its own. It goes on only once the
    child holds the pipe: ended before that, the child would leave its reader
    waiting for a writer for ever."""
    held, holding = os.pipe()
    subprocess.Popen([sys.executable, "-c", CHILD, pipe, str(holding)], pass_fds=(holding,),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(holding)
    os.read(held, 1)
    os.close(held)


def act(ns: argparse.Namespace, project: Path, plan: dict[str, Any], out: Any) -> int:
    """What the plan says, then the turn's end. The exit code."""
    seen = plan.get("seen")
    if seen:
        Path(seen).write_text(json.dumps({
            "request": ns.prompt, "cd": ns.cd, "sandbox": ns.sandbox,
            "env": {k: os.environ.get(k) for k in ("CLAUDECODE", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED")},
        }), encoding="utf-8")
    out.write(STDERR.encode("utf-8"))
    events = Events(out)
    do = plan.get("do", "verdict")
    if do == "fail":
        # A turn that could not run, as its events give it: the exit code is
        # not 0 then.
        events.write({"type": "thread.started", "thread_id": "01a1f409-cb24-7470-8978-1801328c40fe"})
        events.write({"type": "turn.started"})
        events.write({"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again later."}})
        return 1
    prepare(project, plan, events)
    if plan.get("child_pipe"):
        _leave_a_child(plan["child_pipe"])
    if do in ("ask", "hang"):
        if do == "ask":
            # It asks, the question is taken, and it sleeps for the answer.
            events.said(QUESTION)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 1
    if do == "refused":
        events.ran("git commit -am wip", "fatal: Unable to create '.git/index.lock': Operation not permitted\n", 128)
        events.ran("./gradlew --version | tail -3", "Could not open init generic class cache: Operation not "
                   "permitted\n", 1)
        events.ran("ls app/src/main", "", 1)                    # failed, and no sandbox said so
        events.write({"type": "item.completed", "item": {"id": f"item_{events.n}", "type": "file_change",
                                                         "changes": [{"path": "/etc/hosts", "kind": "update"}],
                                                         "status": "failed"}})
        events.said("git and a piped Gradle stayed in the sandbox; I went on without them.")
    elif do == "verdict":
        verdict = {"summary": "The launch waits for the store to be read.",
                   "causes": [{"title": "The store is read on the main thread", "where": [fake_claude.WHERE],
                               "evidence": [{"report": 1, "detector": "main_thread_waits", "location": "worker_pool_#"}],
                               "cost_ms": 700, "cost_of": "the main thread waiting", "fix": "Read it in the background.",
                               "confidence": "medium"}],
                   "cleanup": "removed"}
        (project / VERDICT).write_text(json.dumps(verdict), encoding="utf-8")
        code = fake_claude._echolot(project, "hunt", "--done", "--verdict", VERDICT)
        events.ran(f"echolot hunt --done --verdict {VERDICT}", "closed\n", code)
        events.said("Done: the verdict is recorded." if code == 0 else f"hunt --done said {code}")
    else:
        events.said("I looked at the report and the code, and have nothing to add.")
    events.write({"type": "turn.completed", "usage": USAGE})
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        ns = parse(sys.argv[1:] if argv is None else argv)
    except Refused as e:
        print("error: that is not the command line echolot ui builds", file=sys.stderr)
        return int(e.code or 2)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    plan = json.loads(os.environ.get("FAKE_CODEX") or "{}")
    return act(ns, Path(ns.cd), plan, sys.stdout.buffer)


# ---------------------------------------------------------------------------
# for a test that runs the job in its own process: the same, as `Popen`


class Codex:
    """`subprocess.Popen` for the job, with this script in place of Codex: it
    checks the program and the command line, and does the plan in this
    process. `on_wait` is for a plan that waits ("ask", "hang")."""

    def __init__(self, program: str, plan: dict[str, Any] | None = None, on_wait=None):
        self.program, self.plan, self.on_wait = program, plan or {}, on_wait
        self.argv: list[str] | None = None
        self.env: dict[str, str] | None = None

    def __call__(self, argv, **kw):
        assert argv[0] == self.program, f"the job started {argv[0]}, not the client"
        self.argv, self.env = list(argv), dict(kw["env"])
        ns = parse(argv[1:])
        assert Path(ns.cd) == Path(kw["cwd"]).absolute(), "its working root is the project's folder"
        if self.plan.get("do") in ("ask", "hang"):
            # It works, then waits until it is told to end: a stopped Codex
            # prints no end of its turn.
            out = kw["stdout"]
            out.write(STDERR.encode("utf-8"))
            events = Events(out)
            prepare(Path(kw["cwd"]), self.plan, events)
            if self.plan["do"] == "ask":
                events.said(QUESTION)
            return fake_claude.Process(0, pid=4900, waits=True, on_wait=self.on_wait)
        return fake_claude.Process(act(ns, Path(kw["cwd"]), self.plan, kw["stdout"]), pid=4900)


if __name__ == "__main__":
    raise SystemExit(main())
