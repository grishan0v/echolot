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
import textwrap
from pathlib import Path

from . import hosts, recorder

GUIDE_DIR = Path(__file__).resolve().parent / "guide"
CLAUDE_DIR = Path(__file__).parent / "claude"
# What `init` installed, file by file: the manifest lets `doctor` tell a file
# the project customised from one the package has since moved on from.
LAYER_MANIFEST = "echolot-layer.json"
# Files echolot does not own outright. `.claude/settings.json` is Claude
# Code's, and a project keeps its hooks and its enabled plugins in it; the
# template contributes one permission line and nothing else. Copying the
# template over it — which is what `init --force` did — hands the project
# back that one line and takes the rest of its configuration with it.
MERGED = ("settings.json",)


def sha(path: Path) -> str:
    """Enough of a hash to say: this is not the file we installed."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _fold(template, existing):
    """The template's contribution folded into what the project already has.

    Dictionaries merge key by key, lists gain the entries they are missing,
    and where both sides carry a value the project's wins — it is the
    project's file. Anything the template does not mention is untouched.
    """
    if isinstance(template, dict) and isinstance(existing, dict):
        out = dict(existing)
        for key, value in template.items():
            out[key] = _fold(value, existing[key]) if key in existing else value
        return out
    if isinstance(template, list) and isinstance(existing, list):
        return existing + [v for v in template if v not in existing]
    if existing is None:
        # A key set to null carries no configuration, so filling it in takes
        # nothing away — where the project wrote a value, ours stays out.
        return template
    return existing


def merge(src: Path, dst: Path) -> tuple[str, str | None]:
    """(verdict, the text to write) for a file the project owns too.

    "current" — the template's part is already in there, nothing to do.
    "merged"  — the text to write, the project's own content kept.
    "unreadable" — not JSON we can parse, or not an object; nothing is
                   written. Rewriting a file we could not read is how one
                   permission line would be added at the cost of everything
                   around it.
    """
    try:
        existing = json.loads(dst.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unreadable", None
    if not isinstance(existing, dict):
        return "unreadable", None
    template = json.loads(src.read_text(encoding="utf-8"))
    folded = _fold(template, existing)
    if folded == existing:
        return "current", None
    return "merged", json.dumps(folded, indent=2, ensure_ascii=False) + "\n"


def contribution(src: Path) -> str:
    """The template's part on one line — for the message when we cannot merge."""
    return json.dumps(json.loads(src.read_text(encoding="utf-8")),
                      ensure_ascii=False)


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
        elif what == "ours-without-an-end":
            # Installed before the section had a closing marker, and edited
            # since. Where our part stops is no longer knowable, and the one
            # thing worse than leaving it stale is deleting what is around it.
            print(f"  ≠ {rel} has echolot's section from an older install and "
                  f"has been edited since — left alone")
            print(f"      Put `{hosts.END_MARKER}` on its own line where the "
                  f"section ends, then run `echolot init` again; from then on "
                  f"only that section is updated.")
        elif what == "current":
            print(f"  = {rel} ({host.title}, current)")
        else:
            print(f"  {'↑' if what == 'updated' else '+'} {rel} ({host.title})")

    if manual:
        # The whole section, both markers included and nothing indented, so
        # that what is pasted is exactly what `init` would have written. It
        # used to print four lines and an ellipsis: pasted as shown, the
        # section had no end marker, and every later `init` found an echolot
        # section it could not bound and left it alone as edited.
        print(f"\nAdd this to {', '.join(str(m) for m in manual)} so the agent "
              f"finds the tool — as it is,\nboth marker lines included; `init` "
              f"keeps what is between them current from then on:\n")
        print(hosts.BODY.rstrip("\n"))


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
    # A merged file's hash says nothing: the project's copy is meant to differ,
    # and an install from before the merge existed recorded one anyway.
    for rel in MERGED:
        merged.pop(rel, None)
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
        unreadable   a merged file that is not JSON we can add to

    The manifest is what makes stale and customised distinguishable; a layer
    installed before it existed can only be "differs".

    A file in MERGED is not compared byte for byte — the project's hooks and
    plugins live in it too. The only question about it is whether echolot's
    part is there ("current") or not yet ("stale", which plain `init` fixes).
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
        elif rel in MERGED:
            verdict, _ = merge(src, dst)
            state = "stale" if verdict == "merged" else verdict
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


# Who can bring a file in each state up to date. `init` does stale and
# missing ones on its own and touches nothing the project edited; only `--all`
# updates a file that was edited here, or that nothing can tell from one, and
# it does so by overwriting; and no flag writes over a merged file that does
# not parse. `customised` is in none of them — an edit to a file the package
# has not moved on from asks nothing of anyone.
BY_INIT = ("stale", "missing")
BY_ALL = ("conflict", "differs")
BY_HAND = ("unreadable",)
# Files that may carry the project's own edits, which `init` keeps.
EDITED = ("conflict", "differs", "customised")
# The order the rows are listed in, wherever they are listed.
SHOWN = ("stale", "missing", "conflict", "differs", "customised", "unreadable")


def assess(project: Path) -> dict:
    """The layer's verdict and the one thing to do about it.

    `one_line` (doctor -q, status, init) and `print_status` (the full doctor)
    both render this and decide nothing themselves. Each used to decide for
    itself, and they disagreed: one stale file beside one the project had
    customised read `echolot init` on the one line and `echolot init --all`
    in the full section — and the second would have overwritten the
    customised file to update the stale one.

        absent      nothing installed         `echolot init`
        opted-out   declined on purpose       nothing
        current     nothing to do             nothing
        stale       stale or missing files    `echolot init`
        differs     edited here, or nothing   `echolot init --all`, which
                    can tell                  overwrites — a person decides
        unreadable  settings.json does not    a person fixes the file
                    parse

    In that order when a layer has several: what `init` does on its own comes
    first, so the question a person has to answer is asked about what is left
    after it — and an unreadable settings.json, which `init` can never fix,
    stops being read as a reason to run `init` again. It was: `stale`,
    `next: init`, and `init` left the file as it was every time.
    """
    status = audit(project)
    if status is None:
        # Absent because this project said it does not use Claude Code is a
        # different fact from absent because nobody ran init. Without the
        # distinction, `next` would ask for `echolot init` forever on a
        # project that had just declined the layer.
        if not hosts.wants_claude(project):
            chosen = hosts.load_choice(project) or []
            named = ", ".join(hosts.BY_KEY[k].title for k in chosen) or "nothing"
            return {"verdict": "opted-out", "status": None, "files": {},
                    "command": None,
                    "says": f"not installed — this project points {named} at echolot"}
        return {"verdict": "absent", "status": None, "files": {},
                "command": "echolot init", "says": "none installed here"}
    files: dict[str, list[str]] = {}
    for r in status["rows"]:
        files.setdefault(r["state"], []).append(r["file"])
    if any(s in files for s in BY_INIT):
        verdict, command = "stale", "echolot init"
    elif any(s in files for s in BY_ALL):
        verdict, command = "differs", "echolot init --all"
    elif any(s in files for s in BY_HAND):
        verdict, command = "unreadable", None
    else:
        verdict, command = "current", None
    return {"verdict": verdict, "status": status, "files": files,
            "command": command, "says": None}


def _counts(files: dict[str, list[str]]) -> str:
    return ", ".join(f"{len(files[s])} {s}" for s in SHOWN if s in files)


def one_line(project: Path) -> tuple[str, str]:
    """(verdict, one line) about the project's .claude/ layer — for -q."""
    a = assess(project)
    verdict, files = a["verdict"], a["files"]
    if verdict == "absent":
        return verdict, f"layer: {a['says']} (`{a['command']}`)"
    if verdict == "opted-out":
        return verdict, f"layer: {a['says']}"
    if verdict == "current":
        return verdict, f"layer: current ({len(a['status']['rows'])} files)"
    what = _counts(files)
    if verdict == "stale":
        kept = ("; the files edited here are kept"
                if any(s in files for s in EDITED) else "")
        return verdict, f"layer: STALE — {what} → `{a['command']}`{kept}"
    if verdict == "differs":
        return verdict, (f"layer: STALE — {what} → `{a['command']}` overwrites "
                         f"them, edits made here included — ask first")
    # What to add goes on the line itself: this is the line `status` and
    # `doctor -q` show, and the only other place it is printed is the full
    # doctor run, ten kilobytes for one line of JSON.
    adds = "; ".join(contribution(CLAUDE_DIR / rel) for rel in files[BY_HAND[0]])
    return verdict, ("layer: UNREADABLE — .claude/settings.json is not JSON "
                     "echolot can add to, and no flag of init touches it → "
                     f"fix it by hand, and merge in {adds}")


def print_status(project: Path) -> str | None:
    """The doctor section; returns the one-word verdict for the run log.

    The same verdict and the same command as `one_line`, with the files named.
    """
    a = assess(project)
    verdict, files, status = a["verdict"], a["files"], a["status"]
    print("\n## The .claude/ layer in this project\n")
    if verdict == "absent":
        print(f"  {a['says']}. `{a['command']}` puts the skill, the agent "
              f"and the commands into ./.claude/")
        return verdict
    if verdict == "opted-out":
        print(f"  {a['says']}, as .echolot/hosts.json records — nothing to do.")
        return verdict
    counts = _counts(files)
    print(f"  {len(status['rows'])} template files: "
          + (f"{counts}, " if counts else "")
          + f"{len(files.get('current', []))} current")
    for state in SHOWN:
        for rel in files.get(state, []):
            print(f"    {state:<10} {rel}")
    if status["installed_by"]:
        print(f"  installed by echolot {status['installed_by']}, "
              f"this is {recorder.version()}")
    if verdict == "current":
        print("  the layer is current.")
    elif verdict == "stale":
        print(f"  → `{a['command']}` brings the stale and missing files up to "
              f"date and touches\n    nothing edited here.")
        if any(s in files for s in EDITED):
            print("    The edited files above are kept, and `init` lists them "
                  "again when it runs.")
    elif verdict == "differs":
        if not status["manifest"]:
            print("  installed before echolot kept a manifest, so a file that "
                  "differs cannot be told\n  customised from stale.")
        # Every file marked as edited, not only the ones that asked for it:
        # `--all` does not choose, and a customised file goes with the rest.
        marked = " or ".join(s for s in EDITED if s in files)
        print(textwrap.fill(
            f"`{a['command']}` would overwrite every file marked {marked} above, "
            f"and any edit made here goes with it — ask whoever made them "
            f"first, and carry the edits over from git afterwards. "
            f"settings.json is merged, never overwritten: the project's hooks "
            f"and plugins stay.",
            width=80, initial_indent="  → ", subsequent_indent="    ",
            break_on_hyphens=False, break_long_words=False))
    else:
        for rel in files.get("unreadable", []):
            print(f"  → fix it by hand: .claude/{rel} is not JSON echolot can "
                  f"add to, and it is\n    merged rather than overwritten, so "
                  f"no flag of init touches it. What echolot\n    adds to it: "
                  f"{contribution(CLAUDE_DIR / rel)}")
    return verdict
