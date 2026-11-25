"""`echolot uninit`: what echolot put into a project, taken out again (#562).

`init` writes in more places than anyone keeps in their head: the `.claude/`
layer and its manifest, a permission merged into Claude Code's settings (the
per-machine ones after `--private`), a section between markers in
`AGENTS.md`, `GEMINI.md` and `.github/copilot-instructions.md`, Cursor's rule,
Codex's rule, two lines in `.gitignore` or a block in git's own exclude file,
and the choice of agents under `.echolot/`. An investigation adds temporary
markers to the sources. Taking echolot out by hand meant knowing every one
of them, and a missed one is a stale instruction an agent keeps following.

So this is `init` read backwards, with `init`'s rules:

- A file is deleted only when it is what echolot wrote: the template's bytes,
  or the hash the manifest recorded when `init` wrote it. One edited here is
  kept and named, and `--all` takes it too — the same flag, with the same
  meaning, as `init --all`.
- In a file that is the project's own — settings.json, AGENTS.md, .gitignore,
  the exclude file — only echolot's part is taken out, and the file goes
  only when nothing else was in it.
- After `init --private`, a file git tracks is not touched: that install
  wrote none.
- Nothing is removed before the plan is shown. On a terminal it asks; without
  one `--yes` is the answer, and `--dry-run` shows the plan and stops.

What it keeps unless told: the config (`echolot.yml`, `local.yml`) and
`.echolot/` when it holds a record of the work — traces, reports, an
investigation. Those are the person's, and `--data` takes them too. A
`.echolot/` with nothing in it but the saved choice and the run log is
`init`'s own leavings and goes with the rest. The `.gitignore` lines stay for
as long as what they keep from git is there: taking them away from a folder
of traces is the hazard `ignore.py` exists to close.

It touches nothing outside the project. What echolot keeps elsewhere — the
command itself, trace_processor, a plugin — is named at the end, with how to
remove it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from . import codex, exclude, hosts, ignore, layer, recorder

# What `.echolot/` holds that is echolot's bookkeeping, not a record of the
# work: the choice `init` saved, and the run log with the page's job states
# beside it. Anything else in there — traces, reports, investigations — is.
BOOKKEEPING = (hosts.CHOICE_FILE.name, recorder.LOG_DIR.name)
# The config `setup` built and the machine-local one: the person's, kept
# unless `--data`.
CONFIGS = ("echolot.yml", "local.yml")
# What each `.gitignore` line of echolot's keeps from git, by the pattern.
PROTECTS = {"/.echolot/": ".echolot", "/local.yml": "local.yml"}
# Folders `init` creates around the files it writes outside `.claude/`; each
# goes when nothing is left in it.
FOLDERS = (".cursor/rules", ".cursor", ".github", ".codex/rules", ".codex")
# The page's jobs, by the name each runs under (`check.py`,
# `project_setup.py`, `dig.py`). One running writes into the project while
# this takes it apart.
JOBS = ("setup", "check", "dig")

# The signs, as `init` uses them: `-` goes, `↓` loses echolot's part, `≠` is
# kept, `!` needs a hand.
GOES, LOSES, KEPT, HAND = "-", "↓", "≠", "!"


@dataclass
class Step:
    """One line of the plan, and what doing it is."""

    sign: str
    shown: str
    note: str = ""
    act: Callable[[], Any] | None = None
    # The paths the step deletes, to say what is left once it is done.
    gone: tuple[Path, ...] = ()
    # Lines under it: the files of a group, the places of the markers.
    detail: list[str] = field(default_factory=list)


@dataclass
class Plan:
    project: Path
    steps: list[Step] = field(default_factory=list)
    gone: set[Path] = field(default_factory=set)
    # About things outside the project, or that are not echolot's to remove.
    elsewhere: list[str] = field(default_factory=list)
    markers: int = 0

    def add(self, step: Step) -> None:
        self.steps.append(step)
        self.gone.update(step.gone)

    def acts(self) -> list[Step]:
        return [s for s in self.steps if s.act is not None]

    def left(self, path: Path) -> bool:
        """Whether `path` is still there once the plan is done."""
        if not (path.exists() or path.is_symlink()):
            return False
        return not any(p in self.gone for p in (path, *path.parents))

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.project).as_posix()
        except ValueError:
            return str(path)


# --- the plan -----------------------------------------------------------------

def plan(project: Path, root: Path | None = None, *, everything: bool = False,
         data: bool = False) -> Plan:
    """Everything `uninit` would do here, done to nothing yet.

    `root` is the checkout the markers are looked for in (`project.root`);
    the project folder when not given. `everything` is `--all`, `data` is
    `--data`.
    """
    project = project.resolve()
    p = Plan(project)
    repo = exclude.find(project)
    private = hosts.load_private(project)
    tracked = exclude.tracked(repo, _footprint(project)) if private and repo else set()
    _markers(p, (root or project).resolve())
    _layer(p, everything, tracked)
    _settings(p, tracked, repo)
    _pointers(p, everything, tracked)
    _codex(p, everything, tracked)
    _records(p, data)
    _gitignore(p, tracked)
    _exclude(p, repo)
    _folders(p)
    _elsewhere(p)
    return p


def _footprint(project: Path) -> list[Path]:
    """Every file `init` may write in the project, at either settings name."""
    claude = project / ".claude"
    files = [claude / rel for rel in _template()]
    files += [claude / layer.PRIVATE_SETTINGS, claude / layer.LAYER_MANIFEST]
    files += [project / h.path for h in hosts.HOSTS if h.path and h.key != "claude"]
    files.append(project / ".gitignore")
    return files


def _template() -> dict[str, Path]:
    """The layer's files by their path under `.claude/`."""
    return {src.relative_to(layer.CLAUDE_DIR).as_posix(): src
            for src in layer.template_files()}


def _markers(p: Plan, root: Path) -> None:
    """The lines `mark --apply` wrote into the sources, as `mark --remove` takes them.

    Read here by the rule `mark.remove` applies, over the files it reads
    (C and C++ among them, #1009), so the count is what it will do; the
    removal itself is `mark.remove`, untouched.
    """
    from . import mark

    files: list[tuple[str, int]] = []
    kept: list[str] = []
    try:
        sources = mark.marker_files(root)
    except OSError:
        sources = []
    for path in sources:
        text = mark.text_with(path, (mark.TAG.encode(),))
        if text is None:
            continue
        rows = text.split("\n")
        rest = [ln for ln in rows if not mark.is_applied_line(ln)]
        rel = _shown_under(path, root)
        kept += [f"{rel}:{n}: {ln.strip()[:100]}" for n, ln in enumerate(rest, 1)
                 if mark.is_kept_line(ln)]
        if len(rest) < len(rows):
            files.append((rel, len(rows) - len(rest)))
    where = "" if root == p.project else f" under {root}"
    if files:
        lines = sum(n for _, n in files)
        p.markers = lines
        unwritable: list[str] = []

        def take_out() -> None:
            mark.remove(root, unwritable)
            if unwritable:
                raise OSError(f"could not write {', '.join(unwritable)}")

        p.add(Step(GOES, "temporary markers",
                   f"{lines} line(s) `mark --apply` wrote, in {len(files)} file(s){where}",
                   take_out, detail=[f"{rel}: {n}" for rel, n in files]))
    if kept:
        p.add(Step(HAND, f"`{mark.TAG}` on lines that carry more",
                   "left where they are, as `mark --remove` leaves them: take the "
                   "marker out by hand (the lines numbered as they are once the "
                   "markers above go)", detail=kept))


def _shown_under(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _inside(rel: object) -> bool:
    """A manifest key that names a file under `.claude/` and nowhere else."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return False
    parts = PurePosixPath(rel).parts
    return ".." not in parts and ":" not in parts[0]


def _layer(p: Plan, everything: bool, tracked: set[Path]) -> None:
    """The layer's files: what `init` wrote goes, what was edited here stays.

    Untouched means the template's bytes, or the hash the manifest recorded
    when `init` wrote the file. The second is what lets a layer an older or a
    newer echolot wrote go cleanly: `init` refuses a newer one so as not to
    roll it back, but taking out what that release wrote, as it wrote it,
    rolls nothing back. A file named in the manifest that this release does
    not ship is judged by its hash alone.
    """
    claude = p.project / ".claude"
    manifest = layer._read_manifest(claude)
    installed = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    template = _template()
    rels = sorted((set(template) | {k for k in installed if _inside(k)})
                  - set(layer.MERGED) - {layer.PRIVATE_SETTINGS, layer.LAYER_MANIFEST})
    untouched: list[Path] = []
    held = False
    for rel in rels:
        dst = claude / rel
        if not dst.is_file() or dst.is_symlink():
            continue
        shown = f".claude/{rel}"
        if dst in tracked:
            held = True
            p.add(Step(KEPT, shown, "tracked by git — a private install did not write it"))
            continue
        known = {layer.sha(template[rel])} if rel in template else set()
        if isinstance(installed.get(rel), str):
            known.add(installed[rel])
        if layer.sha(dst) in known:
            untouched.append(dst)
        elif everything:
            p.add(Step(GOES, shown, "edited here — taken with --all",
                       _delete(dst), gone=(dst,)))
        else:
            held = True
            p.add(Step(KEPT, shown, "edited here — kept; `echolot uninit --all` "
                                    "takes it too"))
    if untouched:
        p.add(Step(GOES, ".claude/ layer",
                   f"{len(untouched)} file(s) as init installed them",
                   _delete(*untouched), gone=tuple(untouched)))
    file = claude / layer.LAYER_MANIFEST
    if not file.is_file():
        return
    if file in tracked:
        p.add(Step(KEPT, p.rel(file), "tracked by git — a private install did not write it"))
    elif held:
        # It describes the files that stay: `init` and `uninit --all` judge
        # them by it later.
        p.add(Step(KEPT, p.rel(file), "kept with the layer's files above, which it "
                                      "describes"))
    else:
        p.add(Step(GOES, p.rel(file), "what init installed, file by file",
                   _delete(file), gone=(file,)))


_GONE = object()


def unfold(template: Any, existing: Any) -> Any:
    """`layer._fold` backwards: the project's settings without echolot's part.

    A list loses the entries the template contributes; a key goes when its
    value went whole, and a container goes when taking echolot's part out
    left it empty — one that was empty before stays as it was. A scalar is
    the project's, as `_fold` treats it.
    """
    if isinstance(template, dict) and isinstance(existing, dict):
        out = {}
        for key, value in existing.items():
            left = unfold(template[key], value) if key in template else value
            if left is not _GONE:
                out[key] = left
        return _GONE if existing and not out else out
    if isinstance(template, list) and isinstance(existing, list):
        out_list = [v for v in existing if v not in template]
        return _GONE if existing and not out_list else out_list
    return existing


def _settings(p: Plan, tracked: set[Path], repo: exclude.Repo | None) -> None:
    """The permission `init` merged into Claude Code's settings, at either name."""
    template = json.loads((layer.CLAUDE_DIR / "settings.json").read_text(encoding="utf-8"))
    ours = layer.contribution(layer.CLAUDE_DIR / "settings.json")
    for name in (*layer.MERGED, layer.PRIVATE_SETTINGS):
        path = p.project / ".claude" / name
        if not path.is_file():
            continue
        shown = p.rel(path)
        try:
            raw = path.read_text(encoding="utf-8")
            existing = json.loads(raw)
        except (OSError, ValueError):
            raw, existing = None, None
        if not isinstance(existing, dict):
            if raw is None or "echolot" in raw:
                p.add(Step(HAND, shown, f"not JSON echolot can read — left as it is; "
                                        f"take echolot's part out by hand: {ours}"))
            continue
        left = unfold(template, existing)
        if left == existing:
            continue
        if path in tracked:
            p.add(Step(KEPT, shown, "tracked by git — a private install did not write "
                                    "to it; echolot's permission there is the team's"))
            continue
        if left is _GONE or left == {}:
            p.add(Step(GOES, shown, "it held echolot's permission and nothing else",
                       _delete(path), gone=(path,)))
            continue
        data = (json.dumps(left, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        p.add(Step(LOSES, shown, "echolot's permission taken out; the rest is the "
                                 "project's", _write(path, _as_committed(repo, path, left)
                                                    or data)))


def _as_committed(repo: exclude.Repo | None, path: Path, left: Any) -> bytes | None:
    """The file as the last commit has it, when that is what is left.

    `init` merged its permission in by writing the whole file anew, in its
    own layout. Where the last commit holds the same settings, its bytes go
    back, and git shows nothing; anywhere else the layout `init` left stays.
    """
    if repo is None or not exclude.tracked(repo, [path]):
        return None
    head = exclude.at_head(repo, path)
    try:
        return head if head is not None and json.loads(head) == left else None
    except ValueError:
        return None


def _pointer_hosts() -> list[hosts.Host]:
    return [h for h in hosts.HOSTS if h.pointer and h.path and h.key != "claude"]


_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*(?:\r?\n)*\Z", re.S)


def without_section(text: str) -> str | None:
    """The text with echolot's marked section taken out; None when it has no
    whole section.

    The section goes with its line ending. Where it closed the file, the
    blank line `init` put above it goes too; between two blank lines, one of
    them does. Everything else is the bytes the file had.
    """
    span = hosts._ours(text)
    if span is None:
        return None
    before, after = text[:span[0]], text[span[1]:]
    for eol in ("\r\n", "\n"):
        if after.startswith(eol):
            after = after[len(eol):]
            break
    eol = "\r\n" if "\r\n" in text else "\n"
    if not after.strip():
        body = before.rstrip("\r\n")
        return body + eol if body.strip() else ""
    if not before.strip():
        return after.lstrip("\r\n")
    if before.endswith(eol + eol) and after.startswith(eol):
        after = after[len(eol):]
    return before + after


def _pointers(p: Plan, everything: bool, tracked: set[Path]) -> None:
    """echolot's section in each pointer file, whichever agents are chosen now:
    one deselected since still has it."""
    for host in _pointer_hosts():
        path = p.project / host.path
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_bytes().decode("utf-8", "surrogateescape")
        except OSError:
            continue
        if hosts.MARKER not in text:
            continue
        shown = p.rel(path)
        if path in tracked:
            p.add(Step(KEPT, shown, "tracked by git — a private install did not write "
                                    "to it; echolot's section there is the team's"))
            continue
        rest = without_section(text)
        if rest is None:
            if hosts._untouched_without_an_end(text, host):
                p.add(Step(GOES, shown, "an older echolot's pointer, untouched",
                           _delete(path), gone=(path,)))
            else:
                p.add(Step(HAND, shown,
                           "echolot's section from an older install, edited since and "
                           "with no end line: where it stops is not known, so it is "
                           f"left — take it out by hand, up to where `{hosts.MARKER}`'s "
                           "section ends"))
            continue
        if isinstance(host, hosts.Cursor) and _FRONTMATTER.match(rest):
            # The rule's frontmatter is all that is left: a rule with no
            # body. As `init` wrote it, the file goes; edited, it is kept
            # whole rather than left as a header without a rule.
            front = host.render()[:host.render().index(hosts.MARKER)]
            if rest.replace("\r\n", "\n").strip() == front.strip() or everything:
                p.add(Step(GOES, shown, "echolot's rule" + (
                    " — its frontmatter edited here, taken with --all"
                    if rest.replace("\r\n", "\n").strip() != front.strip() else ""),
                    _delete(path), gone=(path,)))
            else:
                p.add(Step(KEPT, shown, "echolot's rule with its frontmatter edited "
                                        "here — kept whole; `--all` takes it"))
            continue
        if not rest.strip():
            p.add(Step(GOES, shown, "it held echolot's section and nothing else",
                       _delete(path), gone=(path,)))
            continue
        p.add(Step(LOSES, shown, "echolot's section taken out; the rest is the "
                                 "project's",
                   _write(path, rest.encode("utf-8", "surrogateescape"))))


def _codex(p: Plan, everything: bool, tracked: set[Path]) -> None:
    """Codex's rule: echolot's file whole, judged as `init` judges it."""
    state = codex.rule_state(p.project)
    path = p.project / codex.RULE_PATH
    if state == "missing":
        return
    if path in tracked:
        p.add(Step(KEPT, codex.RULE_SHOWN, "tracked by git — a private install did "
                                           "not write it"))
    elif state in ("current", "stale"):
        p.add(Step(GOES, codex.RULE_SHOWN, "lets echolot, ./gradlew and adb out of "
                                           "Codex's sandbox", _delete(path), gone=(path,)))
    elif everything:
        p.add(Step(GOES, codex.RULE_SHOWN, "edited here — taken with --all",
                   _delete(path), gone=(path,)))
    else:
        p.add(Step(KEPT, codex.RULE_SHOWN, "edited here — kept; `echolot uninit --all` "
                                           "takes it too"))


def _size(folder: Path) -> tuple[int, int]:
    """(files, bytes) under a folder, links not followed."""
    files = size = 0
    for top, _, names in os.walk(folder):
        for name in names:
            files += 1
            try:
                size += os.lstat(os.path.join(top, name)).st_size
            except OSError:
                pass
    return files, size


def _amount(files: int, size: int) -> str:
    mb = size / 1e6
    return f"{files} file(s), {mb:.0f} MB" if mb >= 1 else f"{files} file(s)"


def _records(p: Plan, data: bool) -> None:
    """`.echolot/` and the configs: the bookkeeping goes, the work stays unless `--data`."""
    folder = p.project / ".echolot"
    if folder.is_symlink():
        if data:
            p.add(Step(GOES, ".echolot", "a link — the link goes, not what it points at",
                       _delete(folder), gone=(folder,)))
        else:
            p.add(Step(KEPT, ".echolot", "a link to the records — kept; `--data` "
                                         "takes the link"))
    elif folder.is_dir():
        names = sorted(x.name for x in folder.iterdir())
        records = [n for n in names if n not in BOOKKEEPING]
        if data or not records:
            note = (f"traces, reports, investigations and the run log: "
                    f"{_amount(*_size(folder))}" if records
                    else "the saved choice of agents and the run log")
            p.add(Step(GOES, ".echolot/", note, _tree(folder), gone=(folder,)))
        else:
            choice = hosts.choice_path(p.project)
            if choice.is_file():
                p.add(Step(GOES, p.rel(choice), "the choice of agents init saved",
                           _delete(choice), gone=(choice,)))
            shown = ", ".join(n + ("/" if (folder / n).is_dir() else "") for n in records)
            p.add(Step(KEPT, ".echolot/", f"the record of the work ({shown}; "
                                          f"{_amount(*_size(folder))}) — kept, with the "
                                          f"run log; `--data` takes it"))
    for name in CONFIGS:
        path = p.project / name
        if not (path.is_file() or path.is_symlink()):
            continue
        what = "the config" if name == CONFIGS[0] else "the machine-local config"
        if data:
            p.add(Step(GOES, name, what, _delete(path), gone=(path,)))
        else:
            p.add(Step(KEPT, name, f"{what} — kept; `--data` takes it"))


def _gitignore(p: Plan, tracked: set[Path]) -> None:
    """The lines `ignore.ensure` appended, once what they keep from git is gone."""
    path = p.project / ".gitignore"
    if not path.is_file() or path.is_symlink():
        return
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return
    lines = text.splitlines(keepends=True)
    heads = [i for i, ln in enumerate(lines) if ln.strip() == ignore.HEADER]
    if not heads:
        return
    drop: set[int] = set()
    stay: list[str] = []
    for i in heads:
        j = i + 1
        block = []
        while j < len(lines) and lines[j].strip() in PROTECTS:
            block.append(j)
            j += 1
        still = [k for k in block if p.left(p.project / PROTECTS[lines[k].strip()])]
        stay += [lines[k].strip() for k in still]
        if still:
            drop.update(k for k in block if k not in still)
            continue
        drop.update([i, *block])
        # The blank line `ensure` put above the block in a file with lines.
        if i > 0 and not lines[i - 1].strip() and i - 1 not in drop:
            drop.add(i - 1)
    if stay:
        kept = ", ".join(PROTECTS[s] + ("/" if s.endswith("/") else "") for s in stay)
        p.add(Step(KEPT, ".gitignore", f"echolot's {', '.join(stay)} — kept while "
                                       f"{kept} is here; `--data` takes both"))
    if not drop:
        return
    if path in tracked:
        p.add(Step(KEPT, ".gitignore", "tracked by git — a private install did not "
                                       "write to it; echolot's lines there are the team's"))
        return
    rest = "".join(ln for k, ln in enumerate(lines) if k not in drop)
    if not rest.strip():
        p.add(Step(GOES, ".gitignore", "it held echolot's lines and nothing else",
                   _delete(path), gone=(path,)))
    else:
        p.add(Step(LOSES, ".gitignore", "echolot's lines taken out",
                   _write(path, rest.encode("utf-8"))))


def _exclude(p: Plan, repo: exclude.Repo | None) -> None:
    """The block `init --private` wrote in git's own exclude file.

    Only this project's lines leave it, and only for what is gone: the block
    is shared with every checkout of the repository, and with any other
    project in it. While another checkout has a private install here, the
    block is what keeps that one from git, and it stays (#608).
    """
    if repo is None:
        return
    lines = exclude.read(repo)
    if lines is None:
        try:
            text = repo.exclude.read_bytes().decode("utf-8", "surrogateescape")
        except OSError:
            return
        if exclude.BEGIN.split(":")[0] in text:
            p.add(Step(HAND, repo.show(),
                       f"echolot's block without its end line — left alone: put "
                       f"`{exclude.END}` on its own line where it ends, or delete it"))
        return
    others = exclude.private_elsewhere(repo)
    if others:
        p.add(Step(KEPT, repo.show(),
                   f"echolot's block stays: git reads this file in every checkout, and "
                   f"the block keeps the private install in "
                   f"{', '.join(str(o) for o in others)} from git"))
        return
    candidates = [(path, False) for path in _footprint(p.project)]
    candidates += [(p.project / ".claude" / name, False) for name in layer.MERGED]
    candidates += [(p.project / ".echolot", True)]
    candidates += [(p.project / name, False) for name in CONFIGS]
    drop = set()
    for path, folder in candidates:
        if p.left(path):
            continue
        try:
            drop.add(repo.pattern(path, directory=folder))
        except ValueError:
            continue
    rest = [ln for ln in lines if ln not in drop]
    if rest == lines:
        return
    if rest:
        p.add(Step(LOSES, repo.show(),
                   f"{len(lines) - len(rest)} of echolot's lines taken out; the "
                   f"{len(rest)} left keep what stays from git",
                   lambda: _said(exclude.write(repo, rest))))
    else:
        p.add(Step(LOSES, repo.show(), "echolot's block taken out",
                   lambda: exclude.remove(repo)))


def _empty_after(p: Plan, folder: Path, folders: set[Path]) -> bool:
    """Whether `folder` holds nothing once the plan is done."""
    if not folder.is_dir() or folder.is_symlink():
        return False
    try:
        children = list(folder.iterdir())
    except OSError:
        return False
    return all(c in p.gone or (c in folders and _empty_after(p, c, folders))
               for c in children)


def _folders(p: Plan) -> None:
    """The folders `init` made, once nothing is left in them.

    Only these: an empty folder of the project's own is not echolot's to
    take. The top one that empties is what the plan shows.
    """
    claude = p.project / ".claude"
    inner = {claude / PurePosixPath(rel).parent for rel in _template()}
    for f in list(inner):
        inner.update(x for x in f.parents if x != claude and claude in x.parents)
    inner.discard(claude)
    folders = {claude, *inner, *(p.project / f for f in FOLDERS)}
    empty = sorted((f for f in folders if _empty_after(p, f, folders)),
                   key=lambda f: len(f.parts), reverse=True)
    if not empty:
        return
    tops = [f for f in empty if f.parent not in empty]

    def prune() -> None:
        for f in empty:
            try:
                f.rmdir()
            except OSError:
                pass

    p.add(Step(GOES, ", ".join(p.rel(f) + "/" for f in sorted(tops)),
               "empty once the files go", prune, gone=tuple(empty)))


def _elsewhere(p: Plan) -> None:
    """What is not echolot's to remove here, or not in the project at all."""
    copies = codex.imported(p.project) + layer.strays(p.project)
    if copies:
        p.elsewhere.append(
            f"copies of echolot's skills another agent's import made, which "
            f"nothing keeps current: {', '.join(copies)} — not removed; delete them "
            f"unless someone here keeps them on purpose")
    from . import plugin
    try:
        found = plugin.for_project(p.project)
    except OSError:
        found = []
    if "claude" in found:
        p.elsewhere.append(
            "Claude Code has echolot's plugin on for this project: /plugin in Claude "
            "Code manages it. A `--scope local` install made from a git worktree has "
            "been seen declared in the main checkout's .claude/settings.local.json, "
            "not the worktree's")
    if "codex" in found:
        p.elsewhere.append("Codex has echolot's plugin: /plugins in Codex manages it")
    if "cursor" in found:
        p.elsewhere.append(f"Cursor's copy of the plugin: delete "
                           f"{plugin.shown(plugin.cursor_copy())}")
    if p.markers:
        p.elsewhere.append("the build on the phone still has the markers: build and "
                           "install the app again")
    p.elsewhere.append("echolot itself: `pipx uninstall echolot` (or `uv tool "
                       "uninstall echolot`); trace_processor in "
                       "~/.local/share/perfetto/prebuilts/, and ~/.cache/echolot/ "
                       "where runner.compose_names is on")


# --- doing it -----------------------------------------------------------------

def _delete(*paths: Path) -> Callable[[], None]:
    def act() -> None:
        for path in paths:
            path.unlink(missing_ok=True)
    return act


def _tree(folder: Path) -> Callable[[], None]:
    def act() -> None:
        shutil.rmtree(folder)
    return act


def _write(path: Path, data: bytes) -> Callable[[], None]:
    def act() -> None:
        path.write_bytes(data)
    return act


def _said(line: str) -> None:
    """`exclude.write` says a failure rather than raising one."""
    if line.startswith("!"):
        raise OSError(line.lstrip("! "))


def show(p: Plan, out=None) -> None:
    out = out or sys.stdout
    for s in p.steps:
        print(f"  {s.sign} {s.shown}" + (f" — {s.note}" if s.note else ""), file=out)
        for line in s.detail[:12]:
            print(f"      {line}", file=out)
        if len(s.detail) > 12:
            print(f"      … and {len(s.detail) - 12} more", file=out)


def apply(p: Plan) -> list[tuple[Step, OSError]]:
    """Do the plan, step by step; a step that fails is named and the rest go on."""
    failed = []
    for s in p.acts():
        try:
            s.act()
        except OSError as e:
            failed.append((s, e))
    return failed


def running(project: Path) -> str | None:
    """The page's job running in this project, by name; None when none is."""
    from . import job
    for name in JOBS:
        state = job.read(project, name)
        if state is not None and state.get("status") == job.RUNNING:
            return name
    return None


def run(found, args) -> int:
    """`echolot uninit`, for the project `main` resolved."""
    project = found.folder
    if not project.is_dir():
        print(f"no such directory: {getattr(args, 'into', None) or project}",
              file=sys.stderr)
        return 2
    if found.found == "above":
        print(f"[i] the project is {project}, where its echolot.yml is — --into names "
              f"another folder", file=sys.stderr)
    busy = running(project)
    if busy:
        said = (f"uninit: refused — the page's `{busy}` job is running in this "
                f"project and writes into it. Stop it on the page, or let it finish, "
                f"then run uninit again.")
        print(said, file=sys.stderr)
        recorder.failed(said)
        return 1
    everything, data = bool(getattr(args, "force", False)), bool(getattr(args, "data", False))
    p = plan(project, found.root, everything=everything, data=data)
    acts = p.acts()
    if not acts:
        if p.steps:
            print(f"Nothing to take out of {project}; what stays, and why:\n")
            show(p)
        else:
            print(f"Nothing of echolot's in {project}.")
        _say_elsewhere(p)
        recorder.note(uninit="nothing")
        return 0
    print(f"What echolot put into {project}:\n")
    show(p)
    if getattr(args, "dry_run", False):
        print("\nNothing removed: --dry-run. `echolot uninit --yes` does it.")
        _say_elsewhere(p)
        recorder.note(uninit="dry-run", steps=len(acts))
        return 0
    if not getattr(args, "yes", False):
        if getattr(args, "interactive", False) and hosts.interactive(sys.stdout):
            try:
                answer = input("\nTake out what is marked - and ↓? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer not in ("y", "yes"):
                print("Nothing removed.")
                recorder.note(uninit="declined")
                return 0
        else:
            said = ("uninit: nothing removed — there is no terminal to ask on. "
                    "`echolot uninit --yes` takes out what is marked - and ↓ above; "
                    "`--dry-run` shows the plan alone")
            sys.stdout.flush()
            print(f"\n{said}", file=sys.stderr)
            recorder.failed(said)
            return 1
    failed = apply(p)
    sys.stdout.flush()
    for step, e in failed:
        print(f"  ! {step.shown}: not done — {e.strerror or e}", file=sys.stderr)
    if failed and _in_codex_sandbox(failed):
        print("    Codex's sandbox keeps .codex/ read-only for the commands it runs: "
              "run `echolot uninit --yes` outside it — an agent asks for that and "
              "the human approves — or in a terminal.", file=sys.stderr)
    held = [s for s in p.steps if s.act is None and s.sign in (KEPT, HAND)]
    if failed:
        print(f"\nDone, apart from {len(failed)} step(s) above.")
    elif held:
        print(f"\nDone. {len(held)} thing(s) above stay, as marked ≠ and !.")
    else:
        print("\nDone: nothing of echolot's is left in the project.")
    _say_elsewhere(p)
    recorder.note(uninit="done", steps=len(acts), failed=len(failed), kept=len(held),
                  data=data, all=everything)
    if not (project / ".echolot").exists():
        # The line `main` writes after every command would put `.echolot/`
        # back the moment it went.
        recorder.skip()
    return 1 if failed else 0


def _in_codex_sandbox(failed: list[tuple[Step, OSError]]) -> bool:
    from . import sandbox
    return sandbox.host() == sandbox.CODEX and any(
        isinstance(e, PermissionError) and s.shown == codex.RULE_SHOWN for s, e in failed)


def _say_elsewhere(p: Plan) -> None:
    if not p.elsewhere:
        return
    print("\nNot touched here:")
    for line in p.elsewhere:
        print(f"  · {line}")
