"""The `.claude/` layer: what `echolot init` installs, and whether it is current.

A project gets a skill, an agent, three commands and their reference material
copied into it. Copies drift — the package moves on, or someone edits a file in
the project — so `init` records a hash per file and every later run compares
against it. That is the whole job: files, hashes, and a verdict. The record
also names the echolot that wrote it, and an older echolot reads that first
and leaves a newer layer alone.

It knows nothing about traces. It lived in main.py next to the detectors for as
long as main.py was the only file there was to live in.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import textwrap
from pathlib import Path

from . import codex, hosts, recorder

GUIDE_DIR = Path(__file__).resolve().parent / "guide"
CLAUDE_DIR = Path(__file__).parent / "claude"
REFERENCES_DIR = CLAUDE_DIR / "skills" / "echolot" / "references"
COMMANDS_DIR = CLAUDE_DIR / "commands"
HUNTER = CLAUDE_DIR / "agents" / "perf-hunter.md"


def guide_topics() -> dict[str, Path]:
    """Every topic `echolot guide` prints, and the one file each is read from.

    The guide's own pages, and then files the layer already has: `setup` and
    `reflect` are the commands of those names, `loop` is what perf-hunter is
    told, and the rest are the references the skill reads. A client without
    the layer — Codex, Cursor, the plugin's skills — gets the text a Claude
    Code session does, from the same file. The guide used to keep its own
    `setup.md` beside the command, and the two had already drifted apart:
    each knew things the other did not (#189).
    """
    topics = {p.stem: p for p in sorted(GUIDE_DIR.glob("*.md"))}
    topics["setup"] = COMMANDS_DIR / "echolot-setup.md"
    topics["reflect"] = COMMANDS_DIR / "echolot-reflect.md"
    topics["loop"] = HUNTER
    for p in sorted(REFERENCES_DIR.glob("*.md")):
        topics.setdefault(p.stem, p)
    return topics


def guide_text(path: Path) -> str:
    """A topic's text as printed: without the frontmatter a subagent file has."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            text = text[end + len("\n---\n"):]
    return text.strip()
# What `init` installed, file by file: the manifest lets `doctor` tell a file
# the project customised from one the package has since moved on from.
LAYER_MANIFEST = "echolot-layer.json"
# Files echolot does not own outright. `.claude/settings.json` is Claude
# Code's, and a project keeps its hooks and its enabled plugins in it; the
# template contributes one permission line and nothing else. Copying the
# template over it — which is what `init --force` did — hands the project
# back that one line and takes the rest of its configuration with it.
MERGED = ("settings.json",)
# Where the permission goes in a private install (`init --private`): Claude
# Code's per-machine settings, which it reads beside settings.json. The
# project's settings.json may be tracked, and a change to a tracked file shows
# in `git status` whatever the exclude file says.
PRIVATE_SETTINGS = "settings.local.json"


def merged_into(rel: str, private: bool) -> str:
    """The file a template file in MERGED is merged into: settings.local.json when private."""
    return PRIVATE_SETTINGS if private and rel in MERGED else rel
# How a person gets a newer echolot: the two tools that install it as a
# command of its own. One string, because the layer line, the full doctor
# section and `next` all say it.
UPGRADE = "`pipx upgrade echolot` (or `uv tool upgrade echolot`)"

# A version the way PEP 440 spells one, as far as this package could ever
# carry it: the release (`0.8.0`), then a pre-release (`0.8.0rc1`, or
# `0.8.0beta1` and the other long spellings), a post-release
# (`0.8.0.post1`), a development release (`0.8.0.dev2`) and a local label
# (`0.8.0+mine`), each optional and in that order, with the separators PEP
# 440 allows between them. Anything else — `0.8.0-1`, a `-SNAPSHOT` — does
# not read as a version. `packaging` reads the whole standard and is not a
# dependency of echolot; for these forms, the order `version_key` gives is
# the one `packaging` gives.
_VERSION = re.compile(r"""
    v?(?P<release>\d+(?:\.\d+)*)
    (?:[-_.]?(?P<pre>alpha|a|beta|b|preview|pre|rc|c)[-_.]?(?P<pre_n>\d+)?)?
    (?P<post>[-_.]?post[-_.]?(?P<post_n>\d+)?)?
    (?P<dev>[-_.]?dev[-_.]?(?P<dev_n>\d+)?)?
    (?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?
""", re.VERBOSE | re.IGNORECASE)
# Each spelling of a pre-release, as its place before the release.
_PRE = {"a": 0, "alpha": 0, "b": 1, "beta": 1,
        "rc": 2, "c": 2, "pre": 2, "preview": 2}


def version_key(version: object) -> tuple | None:
    """What puts two versions in order — None for one that does not read as one.

    The release is compared number by number, which is the part a string
    comparison gets wrong: 0.10.0 comes after 0.9.0. Trailing zeros do not
    count, so 0.8 and 0.8.0 are one version. Around one release, in order:
    its development releases, its pre-releases (a, b, rc), the release, its
    post-releases. A local label is a build of the version it is attached
    to and orders nothing.

    Releases of this package are plain X.Y.Z tags, so the suffixes are here
    for a build that carries one, which must neither be locked out of a
    layer the release before it wrote nor put its own files over the
    release that came after it.
    """
    if not isinstance(version, str):
        return None
    m = _VERSION.fullmatch(version.strip())
    if m is None:
        return None
    release = [int(n) for n in m["release"].split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    if m["pre"]:
        pre = (_PRE[m["pre"].lower()], int(m["pre_n"] or 0))
    elif m["dev"] and not m["post"]:
        pre = (-1, 0)      # 0.8.0.dev1 comes before 0.8.0a1
    else:
        pre = (3, 0)       # the release itself, after every pre-release
    post = int(m["post_n"] or 0) if m["post"] else -1
    dev = int(m["dev_n"] or 0) if m["dev"] else math.inf
    return (tuple(release), pre, post, dev)


def sha(path: Path) -> str:
    """Enough of a hash to say: this is not the file we installed.

    Of the text with its line endings made LF. A Windows checkout with
    `core.autocrlf=true` has the committed layer in CRLF, and every file then
    matched neither the template nor the manifest: an untouched one read as
    edited, and an update asked for `init --all` and a question about edits
    nobody made.
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16]


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


def install_pointers(project: Path, chosen: list, force: bool = False,
                     tracked: frozenset[Path] | set[Path] = frozenset()) -> list[Path]:
    """Tell the other clients this tool exists.

    `.claude/` is a Claude Code mechanism, and in Cursor or Codex it is an
    invisible directory. The CLI worked there all along — it is a program —
    but nothing pointed an agent at it, so the instructions were followed only
    when the model happened to read the file while looking around. Each client
    gets a few lines saying "run `echolot guide`"; the knowledge stays in the
    package rather than being copied per client.

    Codex gets one more file, and not a pointer: the rule that lets echolot
    out of its sandbox. `force` is `--all`, which puts echolot's rule back
    over an edited one the way it does for the files of the layer.

    Returns the files echolot wrote whole: a pointer file it created, or one
    that holds nothing but its section, and Codex's rule. A private install
    keeps those from git. `tracked` names the files git tracks, which a
    private install does not write at all: the section is printed to paste,
    as for a file that is the project's own.
    """

    # The plugin has no file here: its skills arrive with it.
    stubs = [h for h in chosen if h.key != "claude" and h.path]
    if not stubs:
        return []

    print()
    manual = []
    whole: list[Path] = []
    for host in stubs:
        if project / host.path in tracked:
            print(f"  ≠ {host.path} is tracked by git — a private install leaves it "
                  f"alone")
            if host.pointer:
                manual.append((Path(host.path), host))
            continue
        if not host.pointer:
            codex.install(project, [h.key for h in chosen], force)
            if (project / codex.RULE_PATH).exists():
                whole.append(project / codex.RULE_PATH)
            continue
        what, dest = hosts.write_stub(project, host)
        rel = dest.relative_to(project)
        if what == "written" or (
                dest.exists()
                and dest.read_text(encoding="utf-8", errors="replace") == host.render()):
            whole.append(dest)
        if what == "exists-without-ours":
            manual.append((rel, host))
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

    # The whole section, both markers included and nothing indented, so that
    # what is pasted is exactly what `init` would have written. It used to
    # print four lines and an ellipsis: pasted as shown, the section had no
    # end marker, and every later `init` found an echolot section it could
    # not bound and left it alone as edited. One block per text: AGENTS.md
    # carries a line about Codex's sandbox that the other files do not.
    sections: dict[str, list] = {}
    for rel, host in manual:
        sections.setdefault(hosts.section(host.render()), []).append(rel)
    for text, files in sections.items():
        print(f"\nAdd this to {', '.join(str(m) for m in files)} so the agent "
              f"finds the tool — as it is,\nboth marker lines included; `init` "
              f"keeps what is between them current from then on:\n")
        print(text.rstrip("\n"))
    return whole


def _read_manifest(root: Path) -> dict:
    p = root / LAYER_MANIFEST
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Keys with `/` whatever system wrote them. One written on Windows has
    # `skills\echolot\SKILL.md`, which a Mac never found, and the reverse.
    if isinstance(data.get("files"), dict):
        data["files"] = {str(k).replace("\\", "/"): v for k, v in data["files"].items()}
    return data


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
    private = hosts.load_private(project)
    rows = []
    for src in template_files():
        rel = src.relative_to(CLAUDE_DIR).as_posix()
        dst = root / merged_into(rel, private)
        rows.append({"file": rel, "state": _state(rel, src, dst, installed)})
    return {
        "rows": rows,
        "manifest": bool(installed),
        "installed_by": manifest.get("echolot"),
    }


def _state(rel: str, src: Path, dst: Path, installed: dict[str, str]) -> str:
    """One template file's row in `audit`, in the order its docstring lists them."""
    if not dst.exists():
        return "missing"
    if rel in MERGED:
        verdict, _ = merge(src, dst)
        return "stale" if verdict == "merged" else verdict
    t_sha, d_sha = sha(src), sha(dst)
    if d_sha == t_sha:
        return "current"
    if rel not in installed:
        return "differs"
    # The manifest says what was installed: the project's copy still being
    # that means only the template moved, the template still being it means
    # only the project did.
    was = installed[rel]
    if d_sha == was:
        return "stale"
    return "customised" if t_sha == was else "conflict"


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


def ahead(project: Path) -> dict | None:
    """Why this echolot must leave the project's layer alone — None when it need not.

    The manifest has named the echolot that wrote the layer from the first
    day, and nothing read the name back. So a layer that a teammate's newer
    echolot installed and committed met an older one like this: a file
    untouched since then differs from the older package's template, the
    audit called it stale, and `init` — which `/echolot` runs by itself when
    `next` says `init` — put the older file back, wrote the older version
    into the manifest and printed "Layer updated." The newer echolot then
    found its own files stale and put them back in turn, and the layer went
    back and forth with whoever ran `init` last.

    So the name is read first, before any file is compared. A newer release
    wrote the layer: this one writes nothing and says to upgrade. That is
    also the only news of a newer release echolot can give, since it makes
    no network calls: a teammate's commit is what brings it.

    A name that does not read as a version — a hand edit, a spelling this
    release does not know — cannot be put in order with this one, and is
    treated the same way. Leaving the layer alone costs a person one command;
    putting older files over newer ones undoes a teammate's upgrade without
    a word. A manifest with no name in it makes no claim, and the files are
    judged one by one as before; every echolot that wrote a manifest wrote a
    name, so that is a manifest somebody edited.
    """
    by = _read_manifest(project / ".claude").get("echolot")
    if by is None:
        return None
    this = recorder.version()
    theirs, mine = version_key(by), version_key(this)
    readable = theirs is not None and mine is not None
    if readable and theirs <= mine:
        return None
    if readable:
        says = f"written by echolot {by}, a newer release than this {this}"
    else:
        says = (f".claude/{LAYER_MANIFEST} names echolot {by!r} as its writer, "
                f"and this echolot ({this}) cannot compare that with its own "
                f"version")
    return {"by": by, "this": this, "readable": readable, "says": says}


def assess(project: Path) -> dict:
    """The layer's verdict and the one thing to do about it.

    `one_line` (doctor -q, status, init) and `print_status` (the full doctor)
    both render this and decide nothing themselves. Each used to decide for
    itself, and they disagreed: one stale file beside one the project had
    customised read `echolot init` on the one line and `echolot init --all`
    in the full section — and the second would have overwritten the
    customised file to update the stale one.

        newer       written by a newer        an upgrade; `init` refuses
                    echolot, or one whose
                    version does not read
        absent      nothing installed         `echolot init`
        opted-out   declined on purpose       nothing
        current     nothing to do             nothing
        stale       stale or missing files    `echolot init`
        differs     edited here, or nothing   `echolot init --all`, which
                    can tell                  overwrites — a person decides
        unreadable  settings.json does not    a person fixes the file
                    parse

    `newer` is decided before a single file is compared, and whatever the
    files say: against an older template every file the newer release
    changed reads as stale, which is the misreading it exists to stop (see
    `ahead`). The rest in that order when a layer has several: what `init`
    does on its own comes first, so the question a person has to answer is
    asked about what is left after it — and an unreadable settings.json,
    which `init` can never fix, stops being read as a reason to run `init`
    again. It was: `stale`, `next: init`, and `init` left the file as it was
    every time.
    """
    newer = ahead(project)
    if newer:
        return {"verdict": "newer", "status": None, "files": {},
                "command": None, "says": newer["says"], "ahead": newer}
    status = audit(project)
    if status is None:
        # Absent because this project said it does not use Claude Code is a
        # different fact from absent because nobody ran init. Without the
        # distinction, `next` would ask for `echolot init` forever on a
        # project that had just declined the layer.
        if not hosts.wants_claude(project):
            chosen = hosts.load_choice(project) or []
            if "plugin" in chosen:
                says = "not installed — the skills come with the echolot plugin"
            else:
                named = ", ".join(hosts.BY_KEY[k].title for k in chosen) or "nothing"
                says = f"not installed — this project points {named} at echolot"
            return {"verdict": "opted-out", "status": None, "files": {},
                    "command": None, "says": says}
        return {"verdict": "absent", "status": None, "files": {},
                "command": "echolot init", "says": "none installed here"}
    if not hosts.wants_claude(project):
        # A layer an earlier `init` put in, on a project that has since chosen
        # something else: the plugin, most often. `init` keeps its hands off
        # it, so reading its files as stale sent `next` to `init` for good —
        # and with the plugin, Claude Code loads this copy's skills beside the
        # plugin's own. Saying so is all echolot does: the files may be what
        # teammates without the plugin work from (#144).
        chosen = hosts.load_choice(project) or []
        if "plugin" in chosen:
            says = ("installed here and no longer kept current — the skills come "
                    "with the echolot plugin, and Claude Code loads this copy "
                    "beside them: remove the files .claude/echolot-layer.json "
                    "lists, or `echolot init --for claude` to keep the layer "
                    "instead")
        else:
            named = ", ".join(hosts.BY_KEY[k].title for k in chosen) or "nothing"
            says = (f"installed here and no longer kept current — this project "
                    f"points {named} at echolot; `echolot init --for claude` "
                    f"keeps it current again")
        return {"verdict": "opted-out", "status": status, "files": {},
                "command": None, "says": says}
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


# The way out when the manifest names a version this echolot cannot read,
# and upgrading does not change that: the manifest itself is what is wrong.
# Without one, `init` has nothing to vouch for any file, so it adds what is
# missing and keeps every file that differs from the template until a
# person chooses `--all` — `next` says `init-force`, and the skill asks.
_NO_MANIFEST = (f"if this is already the newest echolot, the manifest is what "
                f"is wrong: delete .claude/{LAYER_MANIFEST}, and `echolot init` "
                f"then keeps every file that differs until `--all` is chosen")


# Said on the layer line when `init --private` installed here: a reader of
# `status` should know that the team does not see this install.
PRIVATE_NOTE = "private to this clone: git ignores what init wrote"


def one_line(project: Path) -> tuple[str, str]:
    """(verdict, one line) about the project's .claude/ layer — for -q."""
    verdict, line = _one_line(project)
    if verdict not in ("absent", "newer") and hosts.load_private(project):
        line += f" · {PRIVATE_NOTE}"
    return verdict, line


def _one_line(project: Path) -> tuple[str, str]:
    a = assess(project)
    verdict, files = a["verdict"], a["files"]
    if verdict == "newer":
        if a["ahead"]["readable"]:
            return verdict, (f"layer: NEWER — {a['says']}, which leaves it alone "
                             f"rather than roll it back → upgrade: {UPGRADE}")
        return verdict, (f"layer: VERSION UNREADABLE — {a['says']}; it could be "
                         f"newer, so the layer is left alone → upgrade: "
                         f"{UPGRADE}; {_NO_MANIFEST}")
    if verdict == "absent":
        return verdict, f"layer: {a['says']} (`{a['command']}`)"
    if verdict == "opted-out":
        return verdict, f"layer: {a['says']}"
    if verdict == "current":
        line = f"layer: current ({len(a['status']['rows'])} files)"
        # Kept current, and the plugin chosen beside it: Claude Code loads
        # the skills of both, which nothing else on the line would say.
        if "plugin" in (hosts.load_choice(project) or []):
            line += (" · the echolot plugin is chosen too, and Claude Code "
                     "loads both copies of the skills → `echolot init --for "
                     "plugin` drops the layer's upkeep, or uninstall the plugin")
        return verdict, line
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
    name = merged_into(MERGED[0], hosts.load_private(project))
    return verdict, (f"layer: UNREADABLE — .claude/{name} is not JSON "
                     "echolot can add to, and no flag of init touches it → "
                     f"fix it by hand, and merge in {adds}")


def print_status(project: Path) -> str | None:
    """The doctor section; returns the one-word verdict for the run log.

    The same verdict and the same command as `one_line`, with the files named.
    """
    a = assess(project)
    verdict, files, status = a["verdict"], a["files"], a["status"]
    print("\n## The .claude/ layer in this project\n")
    if verdict == "newer":
        # No files listed: against this older template, every file the
        # newer release changed would be listed as stale.
        if a["ahead"]["readable"]:
            said = (f"{a['says']}. An older echolot would put its own files "
                    f"back over the newer ones, so this one compares nothing "
                    f"here and writes nothing, and `init` refuses.")
            todo = f"upgrade: {UPGRADE}, then run `echolot` again."
        else:
            said = (f"{a['says']}. It could be newer, so this one compares "
                    f"nothing here and writes nothing, and `init` refuses.")
            todo = f"upgrade: {UPGRADE}; {_NO_MANIFEST}."
        print(textwrap.fill(said, width=80, initial_indent="  ",
                            subsequent_indent="  ", break_on_hyphens=False,
                            break_long_words=False))
        print(textwrap.fill(todo, width=80, initial_indent="  → ",
                            subsequent_indent="    ", break_on_hyphens=False,
                            break_long_words=False))
        return verdict
    if verdict == "absent":
        print(f"  {a['says']}. `{a['command']}` puts the skill, the agent "
              f"and the commands into ./.claude/")
        return verdict
    if verdict == "opted-out":
        if status is None:
            print(f"  {a['says']}, as .echolot/hosts.json records — nothing to do.")
        else:
            print(textwrap.fill(f"The layer is {a['says']}.", width=80,
                                initial_indent="  ", subsequent_indent="  ",
                                break_on_hyphens=False, break_long_words=False))
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
    if hosts.load_private(project):
        print(f"  {PRIVATE_NOTE} — `echolot init --shared` hands it to the team")
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
            name = merged_into(rel, hosts.load_private(project))
            print(f"  → fix it by hand: .claude/{name} is not JSON echolot can "
                  f"add to, and it is\n    merged rather than overwritten, so "
                  f"no flag of init touches it. What echolot\n    adds to it: "
                  f"{contribution(CLAUDE_DIR / rel)}")
    return verdict
