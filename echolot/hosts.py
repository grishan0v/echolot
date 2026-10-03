"""Which agent reads what, and the stub that points it at `echolot guide`.

`echolot init` used to install one thing: `.claude/`. In any other client that
is an invisible directory — Cursor reads `.cursor/rules/`, Codex and a growing
set read `AGENTS.md`, none of them read a Claude Code skill. The tool still
worked there, because the CLI is just a program, but nothing told the agent it
existed. The reported symptom was "picks up the instructions sometimes": the
model occasionally found SKILL.md while reading the repository and followed it,
and otherwise improvised.

The knowledge itself is not copied per client. Each stub is a few lines that
say "run `echolot guide`", and the guide is printed by the installed package —
so it cannot go stale in someone's repository the way a committed copy does,
and there is one text to keep right instead of one per client.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from . import codex

# Both ends, and the second one is load-bearing. `AGENTS.md`, `GEMINI.md` and
# `copilot-instructions.md` belong to the project — they are where a team keeps
# its own instructions for its own agents — and this file writes a section into
# them. Without something saying where that section stops, "update the pointer"
# and "replace the file" are the same operation, which is what it used to be.
MARKER = "<!-- echolot -->"
END_MARKER = "<!-- /echolot -->"

_LEAD = f"""{MARKER}
## Performance work: echolot

This project uses [echolot](https://github.com/grishan0v/echolot) to find where
Android startup time goes, from a Perfetto trace down to a place in the code.

**Never open a `.perfetto-trace` yourself** — it is tens of megabytes and
hundreds of thousands of slices. The tool turns it into about twenty rows.

```bash
echolot          # where this project stands, and what to do next
echolot guide    # how to work with it — read this before performance work
```

`echolot guide` is printed by the installed package, so it always matches the
version in use. `echolot guide hunt` finds a regression; `echolot guide setup`
builds the config.
"""

BODY = _LEAD + END_MARKER + "\n"

# AGENTS.md is the file Codex reads, and in Codex the first `analyze` fails
# before the agent has read anything else: the sandbox refuses it a port. The
# pointer says why, so an agent that reads only the pointer is not left to
# guess at a broken install (#191).
_CODEX_NOTE = """
In Codex, the sandbox stops `echolot` until the rule in
`.codex/rules/echolot.rules` lets it out; `echolot` says whether it does, and
the command that writes it.
"""


@dataclass(frozen=True)
class Host:
    key: str
    title: str
    path: str          # where the stub goes, relative to the project
    evidence: tuple[str, ...]   # paths whose presence means this client is in use
    note: str = ""
    # A few lines in a file that may be the project's own, between markers.
    # Not every client's file is one: Codex's rule is echolot's whole file.
    pointer: ClassVar[bool] = True

    def detected(self, project: Path) -> bool:
        return any((project / e).exists() for e in self.evidence)

    def render(self) -> str:
        return BODY


class Agents(Host):
    def render(self) -> str:
        return _LEAD + _CODEX_NOTE + END_MARKER + "\n"


class Cursor(Host):
    def render(self) -> str:
        # Cursor rules carry frontmatter; alwaysApply keeps it in context
        # rather than waiting to be matched by a glob.
        return ("---\n"
                "description: echolot — Android performance, trace to code\n"
                "alwaysApply: true\n"
                "---\n\n" + BODY)


class Codex(Host):
    """The rule that runs `echolot` outside Codex's sandbox, and not a pointer.

    The file is echolot's alone, so it is written whole, by `codex.install`,
    rather than as a section of a file the project keeps its own words in.
    """
    pointer: ClassVar[bool] = False

    def render(self) -> str:
        return codex.RULE_FILE


HOSTS: tuple[Host, ...] = (
    Host(key="claude", title="Claude Code", path=".claude/",
         evidence=(".claude",),
         note="skill, perf-hunter subagent, commands — the loop gets its own context"),
    Agents(key="agents", title="AGENTS.md", path="AGENTS.md",
           evidence=("AGENTS.md", ".codex"),
           note="Codex, and a growing set of clients that read it"),
    # What Codex needs beyond the pointer: its sandbox has no network, and
    # echolot needs localhost. Its own entry, since AGENTS.md is read by many
    # clients and the rule is Codex's alone (#191).
    Codex(key="codex", title="Codex", path=codex.RULE_SHOWN,
          evidence=(".codex",),
          note="the rule that runs echolot outside its sandbox"),
    # Gemini CLI reads GEMINI.md from the project root by default. AGENTS.md
    # reaches it only when someone has set context.fileName in
    # .gemini/settings.json — the docs show it as an example of overriding the
    # default, not as a second default. So the AGENTS.md stub does not cover
    # this client, and it gets its own.
    Host(key="gemini", title="Gemini CLI", path="GEMINI.md",
         evidence=(".gemini", "GEMINI.md"),
         note="its context file — AGENTS.md is not read unless configured"),
    Cursor(key="cursor", title="Cursor", path=".cursor/rules/echolot.mdc",
           evidence=(".cursor", ".cursorrules"),
           note="a rule, always applied"),
    Host(key="copilot", title="GitHub Copilot",
         path=".github/copilot-instructions.md",
         evidence=(".github/copilot-instructions.md",),
         note="repository instructions"),
    # The plugin brings its own skills, in Claude Code and in Codex alike, so
    # there is no file to write for it and no evidence in the tree to find:
    # it is chosen by name, `init --for plugin`, and the plugin's own door
    # does that. What choosing it still buys is everything `init` does
    # besides the layer — the .gitignore lines above all — and a saved
    # choice, so a project with no .claude/ stops reading as one that needs
    # `echolot init` (#189).
    Host(key="plugin", title="the echolot plugin", path="",
         evidence=(),
         note="its skills come with the plugin; nothing is installed here"),
)

BY_KEY = {h.key: h for h in HOSTS}


def detect(project: Path) -> list[Host]:
    """Clients this project shows evidence of. Claude Code is always included.

    Guessing wrong in the generous direction costs a small file nobody reads.
    Guessing wrong in the other direction is the bug this exists to fix, so
    detection is deliberately loose.
    """
    found = [h for h in HOSTS if h.detected(project)]
    if BY_KEY["claude"] not in found:
        found.insert(0, BY_KEY["claude"])
    return found


def parse(spec: str) -> list[Host] | None:
    """`--for claude,cursor`, `--for all`. None when a word names no client."""
    if spec == "all":
        return list(HOSTS)
    keys = [k.strip() for k in spec.split(",") if k.strip()]
    if any(k not in BY_KEY for k in keys):
        return None
    return [BY_KEY[k] for k in keys]


def _ours(text: str) -> tuple[int, int] | None:
    """Where our section starts and ends, or None when it has no end."""
    start = text.find(MARKER)
    if start < 0:
        return None
    end = text.find(END_MARKER, start)
    return None if end < 0 else (start, end + len(END_MARKER))


def section(text: str) -> str:
    """Our section out of a rendered stub, without the host's own preamble.

    Cursor's rule carries frontmatter above the marker, and that frontmatter
    is the project's to edit — putting it back on every run would be the same
    overwriting this whole function exists to stop, one file smaller.
    """
    span = _ours(text)
    return text[span[0]:span[1]] if span else text


def _without_an_end(text: str) -> str:
    """The same stub as an echolot before 0.4.1 wrote it: no end marker."""
    return text.replace("\n" + END_MARKER, "")


def write_stub(project: Path, host: Host) -> tuple[str, Path]:
    """Put the pointer in place. Returns (what happened, path).

    Only our section is written, never the file. `AGENTS.md`,
    `GEMINI.md` and `copilot-instructions.md` are where a project keeps its
    own instructions for its own agents, and `init` is meant to be run again —
    it says so, and it is the one command a person has to know.

    It used to write the whole file whenever the marker was there and the
    content differed. That guarded exactly one case: a file our section had
    never been in. The ordinary sequence — `init` writes the section, the team
    writes its own rules underneath, `init` runs again — deleted the rules,
    reported `updated`, and named nothing.

    So the marked span is replaced in place and everything around it is left
    alone. A file written before the section had an end marker is migrated
    when it is exactly what an earlier echolot wrote, and otherwise left as it
    is: it has content we did not write and no boundary to tell where ours
    stops, and guessing there is how the file was lost the first time.
    """
    dest = project / host.path
    text = host.render()
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        return "written", dest

    current = dest.read_text(encoding="utf-8", errors="replace")
    if MARKER not in current:
        return "exists-without-ours", dest

    span = _ours(current)
    if span is None:
        if current.strip() != _without_an_end(text).strip():
            return "ours-without-an-end", dest
        updated = text
    else:
        updated = current[:span[0]] + section(text) + current[span[1]:]

    if updated == current:
        return "current", dest
    dest.write_text(updated, encoding="utf-8")
    return "updated", dest


# --- the choice, remembered --------------------------------------------------
#
# Without this, deselecting Claude Code is a trap: `.claude/` would be absent,
# `layer.one_line` would call that "absent", and `next` would ask for `echolot
# init` forever — on a project that had just said it does not want the layer.
# And the choice has to be read back as well as written: see `starting_set`.

CHOICE_FILE = Path(".echolot") / "hosts.json"


def choice_path(project: Path) -> Path:
    return project / CHOICE_FILE


def save_choice(project: Path, chosen: list[Host], private: bool | None = None) -> None:
    """The agents chosen, and whether the install is private to this clone.

    `private` None keeps what was saved: a plain `init` — the one the skill
    runs when the layer goes stale — stays private, as a plain `init` keeps
    the agents. The file is under `.echolot/`, which git never sees, so the
    choice belongs to this clone and to no one else's.
    """
    if private is None:
        private = load_private(project)
    data: dict = {"hosts": [h.key for h in chosen]}
    if private:
        data["private"] = True
    try:
        p = choice_path(project)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_private(project: Path) -> bool:
    """Whether `init --private` installed here: git is kept from everything it wrote."""
    try:
        data = json.loads(choice_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("private") is True


def load_choice(project: Path) -> list[str] | None:
    """The keys this project chose, or None when it never chose."""
    try:
        data = json.loads(choice_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    keys = data.get("hosts") if isinstance(data, dict) else None
    return [k for k in keys if k in BY_KEY] if isinstance(keys, list) else None


def wants_claude(project: Path) -> bool:
    """Whether this project expects the .claude/ layer. Never asked means yes."""
    chosen = load_choice(project)
    return "claude" in chosen if chosen is not None else True


def starting_set(project: Path) -> list[Host]:
    """What `init` points at when `--for` does not say: the saved choice.

    Detection only the first time. It used to run every time, and detection
    puts Claude Code in unconditionally — so `init --for cursor` recorded the
    opt-out, and the next plain `init` detected Claude Code again, installed
    `.claude/` and wrote the opt-out over. Saving the choice made it a state;
    reading it back is what makes it one.
    """
    saved = load_choice(project)
    if saved is None:
        return detect(project)
    return [BY_KEY[k] for k in saved]


def keys(project: Path) -> list[str]:
    """The agents a plain `init` points at here, by key."""
    return [h.key for h in starting_set(project)]


# --- the screen --------------------------------------------------------------

def _colour(stream) -> tuple[str, str, str]:
    if os.environ.get("NO_COLOR") or not getattr(stream, "isatty", lambda: False)():
        return "", "", ""
    return "\033[1m", "\033[2m", "\033[0m"


def interactive(stream) -> bool:
    """Ask only when there is certainly somebody there to answer.

    `echolot init` is run by agents, and by `doctor`'s own self-check five
    times over into temporary directories. A prompt appearing there is a hang,
    not a question — so this errs heavily towards silence: a real terminal on
    both ends, and not a CI runner.
    """
    if os.environ.get("CI") or os.environ.get("ECHOLOT_NO_INPUT"):
        return False
    try:
        return sys.stdin.isatty() and stream.isatty()
    except (AttributeError, ValueError):
        return False


def _host_for(token: str) -> Host | None:
    """One typed word to a host, or nothing — the whole vocabulary of `pick()`.

    `isascii()` before `isdigit()` because they disagree: `"²".isdigit()` is
    true and `int("²")` raises, so the pair without the guard turns a pasted
    superscript into a crash rather than an unknown word.
    """
    if token.isascii() and token.isdigit() and 1 <= int(token) <= len(HOSTS):
        return HOSTS[int(token) - 1]
    return BY_KEY.get(token)


def _parse(line: str) -> tuple[list[Host], list[str]]:
    """The hosts a line names, and the words in it that name nothing.

    Split out of `pick()` so the rule can be read and tested without a
    terminal in the way. `pick()` is the only caller; the tests are the only
    other reader.
    """
    picked: list[Host] = []
    bad: list[str] = []
    for token in line.replace(",", " ").split():
        host = _host_for(token)
        if host is None:
            bad.append(token)
        elif host not in picked:
            picked.append(host)
    return picked, bad


def pick(detected: list[Host], stream=sys.stdout,
         found: set[str] | None = None) -> list[Host]:
    """Confirm a guess rather than ask a blank question.

    Detection has already looked at the project, so the common answer is
    Enter. The answer is a typed line and not an arrow-key menu: "1 3", "all"
    and "none" are one keystroke apart, and what was chosen stays readable in
    a transcript an agent reads back later.

    `detected` is what is ticked, and Enter keeps it: the choice the project
    saved last time when there is one, detection otherwise. `found` is what
    the tree shows evidence of now, marked whether ticked or not — a saved
    choice that leaves out a client the tree has since started using is worth
    seeing — and it is the ticked set when not given.

    `readline` is loaded for its effect on `input()` rather than for anything
    called here. Without it the left arrow arrives inside the answer as the
    escape sequence the key sends, so typing "abc", pressing left twice and
    typing "X" reads back with that punctuation in the middle of it instead of
    "aXbc". With it the line edits the way every other prompt on the machine
    does.

    That costs one of the three things the bare read could claim. readline
    takes the terminal out of its line-at-a-time mode while it waits, so a
    process killed mid-prompt can leave it that way. The other two hold: no
    screen is drawn and no menu is entered, and a piped answer still reads
    straight through. The two gates in `interactive()` keep the exposure down
    to a real terminal with a person in front of it.
    """
    # Imported for its effect and not for anything called here: loading it is
    # what puts `input()` behind it. Inside the function because that is the
    # only place the effect is wanted, and in a try because Windows has no
    # readline — there the console edits the line itself.
    try:
        import readline  # noqa: F401
    except ImportError:
        pass

    bold, dim, off = _colour(stream)
    pre = {h.key for h in detected}
    seen = pre if found is None else found
    print(f"\n{bold}Point which agents at echolot?{off}\n", file=stream)
    width = max(len(h.title) for h in HOSTS)
    for i, h in enumerate(HOSTS, 1):
        mark = "✓" if h.key in pre else "·"
        # Not for Claude Code: detection names it whether or not it is there.
        here = "   (found)" if h.key in seen and h.key != "claude" else ""
        print(f"  {i} {mark} {h.title.ljust(width)}  {dim}{h.path}{off}{here}",
              file=stream)
        if h.note:
            print(f"        {' ' * width}{dim}{h.note}{off}", file=stream)

    default = ",".join(str(i) for i, h in enumerate(HOSTS, 1) if h.key in pre)
    print(f'\n  {dim}Enter — keep {default} · numbers — "1 3" · "all" · "none"{off}',
          file=stream)
    # Both interrupts land here: Ctrl-D raises EOFError and Ctrl-C raises
    # KeyboardInterrupt, and either one means the person left without
    # choosing, which is answered with the guess they were already looking at.
    try:
        raw = input("› ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(file=stream)
        return detected

    if not raw:
        return detected
    if raw == "all":
        return list(HOSTS)
    if raw == "none":
        return []
    picked, bad = _parse(raw)
    if bad:
        print(f"  ignored: {', '.join(bad)}", file=stream)
    return picked or detected
