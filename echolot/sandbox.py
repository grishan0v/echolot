"""When an agent's sandbox is what stopped echolot, and how to say so.

echolot needs two things a sandbox may refuse. perfetto asks the system for a
free port on localhost before it starts trace_processor, and talks to it over
HTTP there; every `analyze`, `probe` and the self-check go through that. And
`collect` reaches the phone through the adb server, which listens on
localhost:5037.

Codex runs each command in a sandbox with no network by default, and on macOS
that takes localhost with it: the profile starts from `(deny default)` and
adds no network rule at all. What came out was `self-check: could not run —
[Errno 1] Operation not permitted` from `doctor`, a PermissionError traceback
from `analyze`, and twenty lines of adb's startup log from `collect`. Each read
as a broken install, and a model's next step from any of them was a guess
(#190).

A refusal is recognised by what it is: the free port perfetto asks for,
refused with EPERM or EACCES. Outside a sandbox nothing refuses that. Whose
sandbox it was comes from what the agent leaves in the environment: Codex sets
CODEX_SANDBOX_NETWORK_DISABLED=1 in every command it runs with the network
off, and CODEX_SANDBOX=seatbelt on macOS. The variables only name the
sandbox; nothing is refused in advance on their word, because a rule can have
let `echolot` out of it.
"""

from __future__ import annotations

import errno
import os
import re
from collections.abc import Mapping

from .config import ConfigError

CODEX = "codex"

# What lets `echolot` out of Codex's sandbox without a prompt each time. Tried
# on a live session (#188): with it, `doctor`, `analyze` and `collect` passed,
# in the main thread and in a subagent; without it, all three failed.
CODEX_RULE = 'prefix_rule(pattern = ["echolot"], decision = "allow")'


class SandboxError(ConfigError):
    """A port on localhost, refused the way a sandbox refuses it.

    A ConfigError, like ToolchainError, so every command that opens a trace
    says it as `error: …` with exit 2, where `analyze` used to end in a
    traceback. `host` is whose sandbox it was, when the environment says:
    `codex`, or None.
    """

    def __init__(self, message: str, host: str | None):
        super().__init__(message)
        self.host = host


def host(env: Mapping[str, str] | None = None) -> str | None:
    """Whose sandbox this process runs in, when the agent said: `codex`."""
    env = os.environ if env is None else env
    if env.get("CODEX_SANDBOX_NETWORK_DISABLED") == "1" or env.get("CODEX_SANDBOX"):
        return CODEX
    return None


def refused(e: BaseException) -> bool:
    """A socket refused the way a sandbox refuses one: EPERM or EACCES.

    With no file named, which is what tells it from the same errno raised
    over a file: a trace_processor_shell without its execute bit is a
    PermissionError too, one that names the binary, and a sandbox is not why.
    """
    return (isinstance(e, OSError) and e.errno in (errno.EPERM, errno.EACCES)
            and e.filename is None)


def ways_out(where: str | None) -> list[str]:
    """How to let `echolot` out, for the sandbox it is in.

    Both hosts when the environment names neither: the refusal is certain,
    whose it was is not, and the two lines cost less than a wrong guess.
    """
    if where == CODEX:
        return [
            "approve running echolot outside the sandbox when Codex asks;",
            f"or let it out for good: {CODEX_RULE} in "
            f".codex/rules/echolot.rules, which Codex reads once it trusts "
            f"the project and a session starts, or the same line in "
            f"~/.codex/rules/echolot.rules for every project.",
        ]
    return [
        f"Codex: approve running echolot outside the sandbox when it asks, or "
        f"put {CODEX_RULE} in .codex/rules/echolot.rules;",
        'Claude Code, with its sandbox on: "echolot *" in '
        "sandbox.excludedCommands.",
    ]


def message(what: str, where: str | None) -> str:
    """The whole sentence: what was refused, whose sandbox, the way out."""
    if where == CODEX:
        head = (f"{what}: this command runs in Codex's sandbox, which has no "
                f"network, localhost included.")
    else:
        head = (f"{what}. Outside a sandbox nothing refuses a port on "
                f"localhost, so an agent's sandbox is the likely cause.")
    return "\n".join([
        head,
        "  echolot needs a port on localhost for trace_processor and the adb "
        "server for the phone, so it has to run outside the sandbox:",
        *(f"  - {line}" for line in ways_out(where))])


def trace_processor_refused(e: OSError,
                            env: Mapping[str, str] | None = None) -> SandboxError:
    """The error for the port perfetto asked for before starting trace_processor."""
    where = host(env)
    why = e.strerror or str(e)
    return SandboxError(
        message(f"trace_processor could not get a port on localhost ({why})",
                where), where)


# adb's own words when its server could not open its port, or its client
# could not reach one: `could not install *smartsocket* listener: Operation
# not permitted`, then `cannot connect to daemon`. "daemon" keeps a refusal
# about a device node, which adb words differently, out of it.
_ADB_REFUSED = re.compile(r"Operation not permitted|Permission denied", re.IGNORECASE)


def adb_refused(said: str) -> bool:
    """True when adb's output is its server being refused a port."""
    return "daemon" in said and bool(_ADB_REFUSED.search(said))


def adb_message(last: str, env: Mapping[str, str] | None = None) -> str:
    """The error for an adb whose server could not be started or reached."""
    return message(f"adb could not start or reach its server on localhost "
                   f"({last})", host(env))
