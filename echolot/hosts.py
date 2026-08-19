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

from dataclasses import dataclass
from pathlib import Path

MARKER = "<!-- echolot -->"

BODY = f"""{MARKER}
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
version in use. `echolot guide hunt` is the loop; `echolot guide setup` builds
the config.
"""


@dataclass(frozen=True)
class Host:
    key: str
    title: str
    path: str          # where the stub goes, relative to the project
    evidence: tuple[str, ...]   # paths whose presence means this client is in use
    note: str = ""

    def detected(self, project: Path) -> bool:
        return any((project / e).exists() for e in self.evidence)

    def render(self) -> str:
        return BODY


class Cursor(Host):
    def render(self) -> str:
        # Cursor rules carry frontmatter; alwaysApply keeps it in context
        # rather than waiting to be matched by a glob.
        return ("---\n"
                "description: echolot — Android performance, trace to code\n"
                "alwaysApply: true\n"
                "---\n\n" + BODY)


HOSTS: tuple[Host, ...] = (
    Host(key="claude", title="Claude Code", path=".claude/",
         evidence=(".claude",),
         note="skill, perf-hunter subagent, commands — the loop gets its own context"),
    Host(key="agents", title="AGENTS.md", path="AGENTS.md",
         evidence=("AGENTS.md", ".codex"),
         note="Codex, and a growing set of clients that read it"),
    Cursor(key="cursor", title="Cursor", path=".cursor/rules/echolot.mdc",
           evidence=(".cursor", ".cursorrules"),
           note="a rule, always applied"),
    Host(key="copilot", title="GitHub Copilot",
         path=".github/copilot-instructions.md",
         evidence=(".github/copilot-instructions.md",),
         note="repository instructions"),
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
    """`--for claude,cursor`, `--for all`, `--for detected`. None when unknown."""
    if spec == "all":
        return list(HOSTS)
    keys = [k.strip() for k in spec.split(",") if k.strip()]
    if any(k not in BY_KEY for k in keys):
        return None
    return [BY_KEY[k] for k in keys]


def write_stub(project: Path, host: Host) -> tuple[str, Path]:
    """Put the pointer in place. Returns (what happened, path).

    A shared file — AGENTS.md, copilot-instructions.md — may already be the
    project's own. It is never rewritten: the marker says whether our section
    is in there, and when it is not the caller is told what to add rather than
    having its file edited underneath it.
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
    if current.strip() == text.strip():
        return "current", dest
    # Ours, and out of date: the file is a pointer, not something to edit.
    dest.write_text(text, encoding="utf-8")
    return "updated", dest
