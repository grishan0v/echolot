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
import sys
from collections.abc import Mapping
from pathlib import Path

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


def rule_command() -> str:
    """The `init` that writes the rule here, the project's other agents kept."""
    from . import codex, hosts
    return codex.init_command(hosts.keys(Path.cwd()), Path.cwd())


def ways_out(where: str | None) -> list[str]:
    """How to let `echolot` out, for the sandbox it is in.

    Both hosts when the environment names neither: the refusal is certain,
    whose it was is not, and the two lines cost less than a wrong guess.

    The rule is written by `init`, and `init` has to run outside the sandbox
    to write it: Codex keeps `.codex/` read-only for the commands it
    sandboxes, so that an agent cannot grant itself a rule (#191).
    """
    if where == CODEX:
        return [
            "approve running echolot outside the sandbox when Codex asks;",
            f"or let it out for good: `{rule_command()}`, run outside the "
            f"sandbox, writes .codex/rules/echolot.rules, which Codex reads "
            f"once it trusts the project and a session starts; or, for every "
            f"project, {CODEX_RULE} in "
            f"{shown(codex_home(os.environ) / 'rules' / 'echolot.rules')}.",
        ]
    return [
        f"Codex: approve running echolot outside the sandbox when it asks, or "
        f"run `{rule_command()}` outside it, which writes the rule that lets "
        f"echolot out;",
        'Claude Code, with its sandbox on: "echolot *" in '
        "sandbox.excludedCommands.",
    ]


# A rule file names the command it lets out as a list of words. It is
# Starlark: either quote, a trailing comma, the list on lines of its own.
_ECHOLOT_PATTERN = re.compile(r"""\bpattern\s*=\s*\[\s*(["'])echolot\1\s*,?\s*\]""")
_DECISION = re.compile(r"""\bdecision\s*=\s*(["'])(\w+)\1""")


def codex_home(env: Mapping[str, str]) -> Path:
    return Path(env.get("CODEX_HOME") or Path.home() / ".codex").resolve()


def shown(path: Path) -> str:
    """A path the way a person would type it: from here, or from home.

    From home only below it: by the string, `/opt/dev2/…` under a home of
    `/opt/dev` came out as `~2/…`, a path that does not exist.
    """
    try:
        return str(path.relative_to(Path.cwd().resolve()))
    except ValueError:
        pass
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def lets_echolot_out(text: str) -> bool:
    """Whether a rules file lets the `echolot` command out of the sandbox.

    A `prefix_rule` call, not commented out, whose pattern is that one word
    and whose decision is `allow`. Only the pattern was looked for: a rule
    set to `prompt` or `forbidden`, or the one line commented out, still
    counted as letting echolot out; and a rule written in single quotes, with
    its arguments in another order or the list laid out one item a line, did
    not count at all.
    """
    for call in _calls(_uncommented(text), "prefix_rule"):
        decision = _DECISION.search(call)
        if _ECHOLOT_PATTERN.search(call) and decision and decision.group(2) == "allow":
            return True
    return False


def _uncommented(text: str) -> str:
    """Starlark with its `#` comments blanked, strings left as they are."""
    out, quote, i = [], None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                out.append(text[i:i + 2])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _calls(code: str, name: str) -> list[str]:
    """The argument text of every `name(...)` call, parentheses matched."""
    found = []
    for m in re.finditer(r"\b" + re.escape(name) + r"\s*\(", code):
        depth, quote = 1, None
        for j in range(m.end(), len(code)):
            ch = code[j]
            if quote:
                if ch == quote and code[j - 1] != "\\":
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
                if depth == 0:
                    found.append(code[m.end():j])
                    break
    return found


def _first_rule(home: Path) -> Path | None:
    for rules in sorted((home / "rules").glob("*.rules")):
        try:
            if lets_echolot_out(rules.read_text(encoding="utf-8")):
                return rules
        except OSError:
            continue
    return None


def project_rule(start: Path | None = None) -> Path | None:
    """A rule in the project's `.codex/rules/` that lets echolot out.

    From the directory the command ran in up to the project's root, the
    first directory with a `.git` in it, file or directory — where Codex
    bounds a project. Past it, a worktree under `<main>/.claude/worktrees/`
    found the main checkout's rule, which Codex in the worktree never reads.
    """
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        found = _first_rule(d / ".codex")
        if found is not None:
            return found
        if (d / ".git").exists():
            return None
    return None


def home_rule(env: Mapping[str, str] | None = None) -> Path | None:
    """A rule under CODEX_HOME that lets echolot out, in every project."""
    return _first_rule(codex_home(os.environ if env is None else env))


def codex_rule(start: Path | None = None,
               env: Mapping[str, str] | None = None) -> Path | None:
    """The rules file that lets echolot out of Codex's sandbox, when one exists.

    Where Codex looks: `.codex/rules/` in the project, which may sit above the
    directory the command ran in, and the user's own under CODEX_HOME.
    """
    return project_rule(start) or home_rule(env)


def _through_python_m() -> bool:
    """Whether this process is `python -m echolot` rather than `echolot`."""
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    return getattr(spec, "name", None) in ("echolot.__main__", "echolot.main")


def message(what: str, where: str | None) -> str:
    """The whole sentence: what was refused, whose sandbox, the way out."""
    rule = project_rule() if where == CODEX else None
    home = home_rule() if where == CODEX else None
    if rule is not None and home is not None:
        # A rule in a project Codex does not trust is not read, and the one
        # under CODEX_HOME is, in every project: that is the rule that
        # counts then.
        from . import codex
        if codex.trust(rule.parents[2]) != "trusted":
            rule = home
    rule = rule or home
    if rule is not None:
        head = (f"{what}: this command ran in Codex's sandbox although "
                f"{shown(rule)} lets echolot out of it.")
        in_project = not rule.is_relative_to(codex_home(os.environ))
        if in_project:
            # A rule in a project Codex does not trust is never read, and
            # nothing about the command line matters then.
            from . import codex
            level = codex.trust(rule.parents[2])
            if level != "trusted":
                return "\n".join([
                    head,
                    f"  Codex has not read it: it reads a project's rules only "
                    f"once it trusts the project, and "
                    f"{codex.trust_words(level)}. Trust the project when Codex "
                    f"asks, at the start of a session here, or put the same "
                    f"file in {codex.home_rules()}, which Codex reads in every "
                    f"project."])
        # The rule is there and the command was refused all the same: Codex
        # matched the command line against it and it did not match. A glob
        # is the usual reason — the documented `analyze
        # .echolot/traces/*.perfetto-trace` is one — and a live session
        # stopped on it, taking the refusal for a missing rule (#189).
        if _through_python_m():
            # Started as `python -m echolot`: the rule covers a line that
            # starts with the word `echolot`, and its own `not_match` names
            # this one. Blaming a glob sent the agent after one it never had.
            return "\n".join([
                head,
                "  This echolot was started as `python -m echolot`, and the "
                "rule lets out only a command line that starts with `echolot`: "
                "run `echolot` itself, or add a rule for the `python -m` form."])
        # Codex reads rules when a session starts, a rule under CODEX_HOME
        # as much as a project's: one added mid-session is not read yet.
        return "\n".join([
            head,
            "  Codex matches the whole command line against the rule. A glob "
            "such as `*.perfetto-trace`, a pipe, or `&&` with another program "
            "keeps the line in the sandbox: run echolot on its own, with the "
            "trace files named (`ls .echolot/traces` lists them).",
            "  If the line was echolot alone, the session started before "
            "the rule was there: Codex reads rules only when a session "
            "starts."])
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
