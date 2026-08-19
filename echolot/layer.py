"""The `.claude/` layer: what `echolot init` installs, and whether it is current.

A project gets a skill, an agent, three commands and their reference material
copied into it. Copies drift — the package moves on, or someone edits a file in
the project — so `init` records a hash per file and every later run compares
against it. That is the whole job: files, hashes, and a verdict.

It knows nothing about traces. It lived in main.py next to the detectors for as
long as main.py was the only file there was to live in.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import hosts, recorder

GUIDE_DIR = Path(__file__).resolve().parent / "guide"
CLAUDE_DIR = Path(__file__).parent / "claude"
# What `init` installed, file by file: the manifest lets `doctor` tell a file
# the project customised from one the package has since moved on from.
LAYER_MANIFEST = "echolot-layer.json"


def sha(path: Path) -> str:
    """Enough of a hash to say: this is not the file we installed."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def template_files() -> list[Path]:
    """The template, minus hidden files (macOS drops .DS_Store into it)."""
    return [p for p in sorted(CLAUDE_DIR.rglob("*"))
            if p.is_file() and not p.name.startswith(".")]


def install_pointers(project: Path, chosen: list) -> None:
    """Tell the other clients this tool exists.

    `.claude/` is a Claude Code mechanism, and in Cursor or Codex it is an
    invisible directory. The CLI worked there all along — it is a program —
    but nothing pointed an agent at it, so the instructions were followed only
    when the model happened to read the file while looking around. Each client
    gets a few lines saying "run `echolot guide`"; the knowledge stays in the
    package rather than being copied per client.
    """

    stubs = [h for h in chosen if h.key != "claude"]
    if not stubs:
        return

    print()
    manual = []
    for host in stubs:
        what, dest = hosts.write_stub(project, host)
        rel = dest.relative_to(project)
        if what == "exists-without-ours":
            manual.append(rel)
            print(f"  ≠ {rel} exists and is yours — left alone")
        elif what == "current":
            print(f"  = {rel} ({host.title}, current)")
        else:
            print(f"  {'↑' if what == 'updated' else '+'} {rel} ({host.title})")

    if manual:
        print(f"\nAdd this to {', '.join(str(m) for m in manual)} so the agent "
              f"finds the tool:\n")
        for line in hosts.BODY.strip().split("\n")[:4]:
            print(f"    {line}")
        print("    …  (`echolot guide` prints the rest)")


def _read_manifest(root: Path) -> dict:
    p = root / LAYER_MANIFEST
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_manifest(root: Path, files: dict[str, str]) -> None:
    old = _read_manifest(root)
    merged = dict(old.get("files") or {})
    merged.update(files)
    (root / LAYER_MANIFEST).write_text(json.dumps({
        "echolot": recorder.version(),
        "files": dict(sorted(merged.items())),
    }, indent=2) + "\n", encoding="utf-8")


def audit(project: Path) -> dict | None:
    """The project's .claude/ layer against the package's template.

    None when there is no layer here. Otherwise one row per template file:

        current      identical to the template
        stale        untouched since install, and the template has moved on
        customised   edited in the project, the template has not moved
        conflict     edited in the project AND the template has moved on
        differs      not identical, and no manifest to say which of the two
        missing      the template has it, the project does not

    The manifest is what makes stale and customised distinguishable; a layer
    installed before it existed can only be "differs".
    """
    root = project / ".claude"
    if not (root / "skills" / "echolot" / "SKILL.md").exists():
        return None
    manifest = _read_manifest(root)
    installed = manifest.get("files") or {}
    rows = []
    for src in template_files():
        rel = str(src.relative_to(CLAUDE_DIR))
        dst = root / rel
        t_sha = sha(src)
        if not dst.exists():
            state = "missing"
        else:
            d_sha = sha(dst)
            if d_sha == t_sha:
                state = "current"
            elif rel in installed:
                was = installed[rel]
                if d_sha == was:
                    state = "stale"
                elif t_sha == was:
                    state = "customised"
                else:
                    state = "conflict"
            else:
                state = "differs"
        rows.append({"file": rel, "state": state})
    return {
        "rows": rows,
        "manifest": bool(installed),
        "installed_by": manifest.get("echolot"),
    }


def one_line(project: Path) -> tuple[str, str]:
    """(verdict, one line) about the project's .claude/ layer — for -q."""
    status = audit(project)
    if status is None:
        # Absent because this project said it does not use Claude Code is a
        # different fact from absent because nobody ran init. Without the
        # distinction, `next` would ask for `echolot init` forever on a
        # project that had just declined the layer.
        if not hosts.wants_claude(project):
            chosen = hosts.load_choice(project) or []
            named = ", ".join(hosts.BY_KEY[k].title for k in chosen) or "nothing"
            return "opted-out", f"layer: not installed — this project points {named} at echolot"
        return "absent", "layer: none installed here (`echolot init`)"
    by_state: dict[str, int] = {}
    for r in status["rows"]:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
    needs = {k: v for k, v in by_state.items()
             if k in ("stale", "conflict", "differs", "missing")}
    if not needs:
        return "current", f"layer: current ({len(status['rows'])} files)"
    what = ", ".join(f"{v} {k}" for k, v in needs.items())
    # stale and missing files `init` updates on its own; files that differ
    # with no manifest to say why, or that were edited here, need --force.
    if set(needs) <= {"stale", "missing"}:
        return "stale", f"layer: STALE — {what} → `echolot init`"
    return "differs", f"layer: STALE — {what} → `echolot init --force`"


def print_status(project: Path) -> str | None:
    """The doctor section; returns the one-word verdict for the run log."""
    status = audit(project)
    print("\n## The .claude/ layer in this project\n")
    if status is None:
        print("  none installed here. `echolot init` puts the skill, the agent "
              "and the commands into ./.claude/")
        return "absent"
    by_state: dict[str, list[str]] = {}
    for r in status["rows"]:
        by_state.setdefault(r["state"], []).append(r["file"])
    total = len(status["rows"])
    counts = ", ".join(f"{len(v)} {k}" for k, v in by_state.items())
    print(f"  {total} template files: {counts}")
    for state in ("stale", "conflict", "customised", "differs", "missing"):
        for rel in by_state.get(state, []):
            print(f"    {state:<10} {rel}")
    if status["installed_by"]:
        print(f"  installed by echolot {status['installed_by']}, "
              f"this is {recorder.version()}")
    needs_update = set(by_state) & {"stale", "conflict", "differs", "missing"}
    if not needs_update:
        print("  the layer is current.")
        return "current"
    if not status["manifest"]:
        print("  installed before echolot kept a manifest, so a file that differs "
              "cannot be told\n  customised from stale. `echolot init --force` "
              "overwrites; keep the project's edits with git.")
    else:
        print("  → `echolot init --force` updates it. Customised files are "
              "listed above and are\n    overwritten too — carry the edits "
              "over afterwards.")
    return "stale"
