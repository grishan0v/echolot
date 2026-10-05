"""Codex in a project: the rule that lets echolot out of its sandbox, whether
Codex reads it, and the copies its /import leaves behind.

Codex runs commands in a sandbox with no network, and echolot needs localhost
(sandbox.py says why). One rule runs the `echolot` command outside it and
nothing else, and `init --for codex` writes that rule into
`.codex/rules/echolot.rules` (#191). Three things decide whether it works, and
`doctor` and `status` say each of them:

- the file. It is echolot's alone, so `init` writes it whole, and an edit
  made here is kept the way `init` keeps an edited file of the `.claude/`
  layer.
- trust. Codex reads a project's `.codex/` only once the person has said they
  trust the project, and it records that in `~/.codex/config.toml`. The same
  rule in `~/.codex/rules/` is read in every project.
- the moment. Codex reads rules when a session starts; a session already
  running goes on without the new one.

And one thing a command in the sandbox cannot do: write into `.codex/`. Codex
keeps it read-only there, so that an agent cannot grant itself a rule. So
`init --for codex` has to run outside the sandbox, and says so when it could
not write the file.

The copies: Codex's /import turns a Claude Code project's `.claude/` into
skills under `.agents/skills/` and an agent under `.codex/agents/`, and
rewrites `.claude/` to `.Codex/` in them on the way. Nothing keeps them
current. `doctor` names them and deletes nothing: someone may have edited
them on purpose.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import textwrap
from collections.abc import Mapping
from pathlib import Path

from . import sandbox

KEY = "codex"
RULE_PATH = Path(".codex") / "rules" / "echolot.rules"
RULE_SHOWN = RULE_PATH.as_posix()

# The `match` and `not_match` lines are examples Codex checks when it loads
# the file, so a rule that stopped matching what echolot runs would say so
# when a session starts rather than by the sandbox refusing a port.
RULE_FILE = """\
# Written by `echolot init --for codex`. Edit it and `echolot init` leaves it
# as it is; delete it and `echolot init` writes it again.
#
# echolot needs a port on localhost for trace_processor, and the adb server
# for the phone. Codex's sandbox has no network, localhost included, so this
# runs the `echolot` command outside it without asking each time: that
# command alone, not a line that chains it to another program or globs its
# arguments. Codex reads this file when a session starts, in a project it
# trusts; `echolot doctor` says whether it does.
prefix_rule(
    pattern = ["echolot"],
    decision = "allow",
    justification = "echolot needs localhost, for trace_processor and the adb server",
    match = [
        "echolot doctor -q",
        "echolot collect -c echolot.yml -n 5",
        "echolot analyze .echolot/traces/cold-1.perfetto-trace -c echolot.yml",
    ],
    not_match = [
        "python3 -m echolot doctor",
    ],
)
"""

# Hashes of the rule files earlier releases wrote. When RULE_FILE changes, the
# hash of the text it had goes here: a file that still matches one is ours and
# untouched, and `init` brings it up to date instead of keeping it as an edit.
_EARLIER: frozenset[str] = frozenset()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rule_state(project: Path) -> str:
    """`.codex/rules/echolot.rules` against the text this echolot writes.

        missing   not there
        current   exactly RULE_FILE
        stale     what an earlier echolot wrote, untouched since
        edited    anything else
    """
    path = project / RULE_PATH
    if not path.is_file():
        return "missing"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "edited"
    if text == RULE_FILE:
        return "current"
    return "stale" if _sha(text) in _EARLIER else "edited"


def write_rule(project: Path, force: bool = False) -> tuple[str, OSError | None]:
    """Put the rule in place: (what happened, the error when it could not).

    written, updated, current, overwritten (an edit, with `--all`), kept (an
    edit, without it), or not-written.
    """
    state = rule_state(project)
    if state == "current":
        return "current", None
    if state == "edited" and not force:
        return "kept", None
    path = project / RULE_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(RULE_FILE, encoding="utf-8")
    except OSError as e:
        return "not-written", e
    return {"missing": "written", "stale": "updated"}.get(state, "overwritten"), None


# --- whether Codex trusts the project --------------------------------------

def config_file(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return sandbox.codex_home(env) / "config.toml"


def _git_root(start: Path) -> Path | None:
    for d in (start, *start.parents):
        if (d / ".git").exists():
            return d
    return None


def _main_root(root: Path) -> Path | None:
    """The main checkout of a linked worktree, whose trust Codex applies to it."""
    dot_git = root / ".git"
    if not dot_git.is_file():
        return None
    try:
        line = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = (root / line[len("gitdir:"):].strip()).resolve()
    # <main>/.git/worktrees/<name>
    if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
        return gitdir.parent.parent.parent
    return None


def _trust_keys(project: Path) -> list[str]:
    """Where Codex looks a project up, in its order.

    The directory that holds `.codex/`, then the repository it is in, then
    the main checkout when that repository is a linked worktree; each as the
    path resolves and as it is spelled (codex-rs/config/src/loader/mod.rs,
    `decision_for_dir`).
    """
    here = project.absolute()
    places = [here]
    root = _git_root(here)
    if root is not None:
        places.append(root)
        main = _main_root(root)
        if main is not None:
            places.append(main)
    keys: list[str] = []
    for p in places:
        for key in (str(p.resolve()), str(p)):
            if key not in keys:
                keys.append(key)
    return keys


_TABLE = re.compile(r"""^\s*\[\s*projects\s*\.\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)')\s*\]""")
_TRUST = re.compile(r"""^\s*trust_level\s*=\s*["']([A-Za-z_]+)["']""")


def _trust_levels_by_line(text: str) -> dict[str, str]:
    """The `[projects."<path>"]` tables Codex writes, read line by line.

    For Python 3.10, which has no TOML reader of its own. Codex writes each
    project as a table of its own with `trust_level` inside, which is all
    this reads.
    """
    levels: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            m = _TABLE.match(line)
            if m is None:
                current = None
            elif m[1] is not None:
                current = m[1].replace('\\"', '"').replace("\\\\", "\\")
            else:
                current = m[2]
            continue
        m = _TRUST.match(line)
        if m and current is not None:
            levels[current] = m[1]
    return levels


def _trust_levels(text: str) -> dict[str, str]:
    """Each project in Codex's config, and how it is marked."""
    try:
        import tomllib
    except ImportError:
        return _trust_levels_by_line(text)
    try:
        projects = tomllib.loads(text).get("projects")
    except tomllib.TOMLDecodeError:
        return {}
    if not isinstance(projects, dict):
        return {}
    return {key: entry["trust_level"] for key, entry in projects.items()
            if isinstance(entry, dict) and isinstance(entry.get("trust_level"), str)}


def trust(project: Path, env: Mapping[str, str] | None = None) -> str | None:
    """`trusted` or `untrusted`, as Codex's config marks this project; None
    when it does not mark it, or there is no config to read."""
    try:
        text = config_file(env).read_text(encoding="utf-8")
    except OSError:
        return None
    levels = _trust_levels(text)
    if os.name == "nt":
        # Codex looks paths up without case on Windows.
        levels = {k.lower(): v for k, v in levels.items()}
    for key in _trust_keys(project):
        level = levels.get(key.lower() if os.name == "nt" else key)
        if level is not None:
            return level
    return None


def trust_words(level: str | None, env: Mapping[str, str] | None = None) -> str:
    shown = sandbox.shown(config_file(env))
    if level == "untrusted":
        return f"{shown} marks this project untrusted"
    return f"{shown} does not list this project as trusted"


def home_rules(env: Mapping[str, str] | None = None) -> str:
    """Where a rule for every project goes, as a person would type it."""
    env = os.environ if env is None else env
    return sandbox.shown(sandbox.codex_home(env) / "rules") + "/"


# --- the copies /import leaves ---------------------------------------------

def imported(project: Path) -> list[str]:
    """Copies of echolot's skills and subagent where Codex's /import puts them."""
    found = []
    skills = project / ".agents" / "skills"
    if (skills / "echolot" / "SKILL.md").is_file():
        found.append(".agents/skills/echolot/")
    for d in sorted(skills.glob("source-command-echolot-*")):
        if (d / "SKILL.md").is_file():
            found.append(f".agents/skills/{d.name}/")
    if (project / ".codex" / "agents" / "perf-hunter.toml").is_file():
        found.append(".codex/agents/perf-hunter.toml")
    return found


# --- the verdict -------------------------------------------------------------

def in_use(project: Path, keys: list[str], env: Mapping[str, str] | None = None) -> bool:
    """Whether Codex is worth a line here: chosen, found, or running this."""
    return (KEY in keys or (project / ".codex").is_dir()
            or sandbox.host(env) == sandbox.CODEX or bool(imported(project)))


def init_command(keys: list[str], project: Path | None = None) -> str:
    """The `init` that writes the rule and keeps the rest of the choice.

    `--for` replaces the choice, so `--for codex` alone on a project that
    chose Claude Code would stop keeping its `.claude/` layer current. And
    the whole list even when codex is in it already: the plugin's door
    forbids a plain `echolot init`, which would install `.claude/` on a
    project that never chose (#189).

    On a project that never chose, the list is detection's, and detection
    puts Claude Code in whatever is there — so the advice installed
    `.claude/` beside a plugin that brings the same skills. Then Claude Code
    is left out, and AGENTS.md, which Codex reads, stands in for it.
    """
    from . import hosts  # late: hosts imports this module
    if project is not None and hosts.load_choice(project) is None:
        keys = [k for k in keys if k != "claude"]
        if "agents" not in keys:
            keys = ["agents", *keys]
    return "echolot init --for " + ",".join([*(k for k in keys if k != KEY), KEY])


def assess(project: Path, keys: list[str],
           env: Mapping[str, str] | None = None) -> dict | None:
    """Codex in this project, or None when nothing says it is used here.

        current     a rule in the project lets echolot out, and Codex trusts
                    the project
        home        a rule in ~/.codex/rules/ lets echolot out, everywhere
        untrusted   a rule in the project, and Codex does not read it there
        missing     nothing lets echolot out
        edited-out  echolot.rules was edited and lets echolot out no more
    """
    env = os.environ if env is None else env
    if not in_use(project, keys, env):
        return None
    state = rule_state(project)
    found = sandbox.project_rule(project)
    home = sandbox.home_rule(env)
    own = found is not None and found == (project / RULE_PATH).resolve()
    level = None
    if found is not None:
        # The project's own rule by the project's path, as `install` looks it
        # up, both spellings; a rule found above it, by where it sits.
        level = trust(project if own else found.parents[2], env)
    if found is not None and level == "trusted":
        verdict = "current"
    elif home is not None:
        # Read in every project, whatever the project's own rule is: one
        # Codex does not trust no longer hides it.
        found, own, verdict = home, False, "home"
    elif found is not None:
        verdict = "untrusted"
    else:
        verdict = "edited-out" if state == "edited" else "missing"
    return {"verdict": verdict, "state": state, "found": found, "own": own,
            "trust": level, "copies": imported(project),
            "command": init_command(keys, project)}


def one_line(project: Path, keys: list[str],
             env: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """(verdict, one line) for `status` and `doctor -q`; None when Codex is
    not used here."""
    a = assess(project, keys, env)
    if a is None:
        return None
    verdict, found = a["verdict"], a["found"]
    shown = sandbox.shown(found) if found is not None else RULE_SHOWN
    if verdict == "current":
        line = f"{shown} lets echolot out of the sandbox, and Codex trusts this project"
        if a["state"] == "stale" and a["own"]:
            line += f"; it is from an older echolot → `{a['command']}`"
    elif verdict == "home":
        line = f"{shown} lets echolot out of the sandbox, in every project"
    elif verdict == "untrusted":
        line = (f"{shown} is not read: Codex reads a project's rules once it "
                f"trusts the project, and {trust_words(a['trust'], env)} → trust "
                f"it when Codex asks, at the start of a session here, or put the "
                f"same file in {home_rules(env)}")
    elif verdict == "edited-out":
        line = (f"{RULE_SHOWN} was edited and lets echolot out of the sandbox no "
                f"more → delete it, then `{a['command']}`, outside the sandbox")
    else:
        line = (f"NO RULE — nothing lets echolot out of the sandbox → "
                f"`{a['command']}`, run outside it: the sandbox keeps .codex/ "
                f"read-only")
    if a["copies"]:
        line += (f" · {len(a['copies'])} copies of echolot's skills that nothing "
                 f"keeps current (`echolot doctor` lists them)")
    return verdict, line


def _say(text: str, first: str = "  ", rest: str = "  ") -> None:
    print(textwrap.fill(text, width=80, initial_indent=first, subsequent_indent=rest,
                        break_on_hyphens=False, break_long_words=False))


def print_status(project: Path, keys: list[str],
                 env: Mapping[str, str] | None = None) -> str | None:
    """The doctor section; the verdict for the run log, None when not shown."""
    env = os.environ if env is None else env
    a = assess(project, keys, env)
    if a is None:
        return None
    verdict, found = a["verdict"], a["found"]
    print("\n## Codex in this project\n")
    if verdict in ("current", "untrusted"):
        edited = {"edited": ", edited here,", "stale": ", from an older echolot,"}
        own = a["own"]
        _say(f"{sandbox.shown(found)}{edited.get(a['state'], '') if own else ''} "
             f"lets echolot out of the sandbox.")
        if verdict == "current":
            _say(f"{sandbox.shown(config_file(env))} trusts this project, so Codex "
                 f"reads the rule when a session starts.")
        else:
            _say(f"Codex reads a project's rules only once it trusts the project, "
                 f"and {trust_words(a['trust'], env)}. Trust it when Codex asks, at "
                 f"the start of a session here, or put the same file in "
                 f"{home_rules(env)}, which Codex reads in every project.", first="  → ", rest="    ")
        if a["state"] == "stale" and own:
            _say(f"`{a['command']}` brings it up to date.", first="  → ", rest="    ")
    elif verdict == "home":
        _say(f"{sandbox.shown(found)} lets echolot out of the sandbox, in every "
             f"project.")
    else:
        if verdict == "edited-out":
            _say(f"{RULE_SHOWN} was edited here and no longer lets echolot out of "
                 f"the sandbox, so doctor, analyze and collect fail in it.")
            todo = f"delete it, then run `{a['command']}`"
        else:
            _say("Nothing lets echolot out of the sandbox, so doctor, analyze and "
                 "collect fail in it.")
            todo = f"`{a['command']}` writes {RULE_SHOWN}"
        _say(f"{todo}. Run it outside the sandbox: Codex keeps .codex/ read-only "
             f"for the commands it runs. Or, for every project: "
             f"{sandbox.CODEX_RULE} in {home_rules(env)}echolot.rules.",
             first="  → ", rest="    ")
    if a["copies"]:
        print("\n  Copies of echolot's skills that nothing keeps current:")
        for rel in a["copies"]:
            print(f"    {rel}")
        _say("Codex's /import makes them from .claude/ and rewrites .claude/ in "
             "them to .Codex/, so they point at files that are not there. The "
             "echolot plugin replaces them: `codex plugin marketplace add "
             "grishan0v/echolot`. echolot deletes nothing — remove them once the "
             "plugin is in, unless someone here edited them on purpose.")
    return verdict


def install(project: Path, keys: list[str], force: bool = False,
            env: Mapping[str, str] | None = None) -> str:
    """`init`'s part: write the rule, and say what Codex will do with it."""
    env = os.environ if env is None else env
    what, error = write_rule(project, force)
    rel = RULE_SHOWN
    if what == "current":
        print(f"  = {rel} (Codex, current)")
        return what
    if what == "kept":
        print(f"  ≠ {rel} was edited here — left alone; delete it, and the next "
              f"`echolot init` writes echolot's")
        return what
    if what == "not-written":
        refused = (isinstance(error, PermissionError)
                   or getattr(error, "errno", None) in (errno.EPERM, errno.EACCES))
        if refused and sandbox.host(env) == sandbox.CODEX:
            _say(f"! {rel} not written: Codex's sandbox keeps .codex/ read-only "
                 f"for the commands it runs, so that none of them can let itself "
                 f"out. Run `{init_command(keys)}` outside the sandbox — an agent "
                 f"asks for that and the human approves — or in a terminal.",
                 first="  ", rest="      ")
        else:
            _say(f"! {rel} not written: {error}", first="  ", rest="      ")
        return what
    mark = {"written": "+", "updated": "↑", "overwritten": "!"}[what]
    note = ("was edited here — overwritten" if what == "overwritten"
            else "Codex: runs echolot outside its sandbox")
    print(f"  {mark} {rel} ({note})")
    level = trust(project, env)
    if level == "trusted":
        _say("Codex reads it when a session starts: one already running keeps "
             "echolot in the sandbox until it is restarted.",
             first="      ", rest="      ")
    elif sandbox.home_rule(env) is not None:
        # The advice below is to do what is done already.
        _say(f"Codex reads a project's rules only once it trusts the project — "
             f"{trust_words(level, env)} — and the rule in {home_rules(env)} "
             f"lets echolot out here in the meantime.",
             first="      ", rest="      ")
    else:
        _say(f"Codex reads it when a session starts, and only in a project it "
             f"trusts — {trust_words(level, env)}: trust it when Codex asks, or "
             f"put the same file in {home_rules(env)}.",
             first="      ", rest="      ")
    return what
