"""Recordings made elsewhere, read from `echolot ui` (#479).

The button records on the phone, or reads the recordings `collect` left under
`.echolot/traces/`. Recordings made somewhere else had no way in from the
page: a CI artefact someone downloaded, the output folder of a macrobenchmark
(#154), a recording a colleague sent. **Analyse trace files** on the start
page takes them in three steps:

1. A person types the folder. A page in a browser cannot hand a path to the
   server through a file field, only the bytes, and recordings are tens of
   megabytes each: the field is text.
2. The page says what it found there (`look`), before anything is read: how
   many recordings, of which scenario or test where their names say, how
   large and how recent, and whether the names repeat, which is several
   sittings in one folder.
3. A press of Read them (`start`) runs `analyze` over the recordings chosen,
   as the button runs it, so the report is the one the terminal gives.

Three things decided here:

- **What a named folder can give.** The folder is the first path a request
  to the server names. The server lists it, one level deep, and takes in
  only files whose names end in `.perfetto-trace`, `.pftrace` or `.trace`:
  their names, sizes and times. It opens none of them. The files of the
  recordings chosen go to `analyze` by their paths, and the report is
  written under the project's `.echolot/`, never in the folder named. A
  folder inside is only counted, so that a folder of sets can say which set
  to name. A path is text of at most `PATH_CAP` characters with no control
  character in it, absolute, from the home folder (`~`), or from the
  project's folder.
- **Whose report it is.** Someone else's recordings read as the report of
  this project's scenario would stand beside this phone's runs in the next
  comparison. They become a report of their own, in `.echolot/out/elsewhere`
  (`OUT`): the scenarios' reports, and what Check again compares with, stay
  as they were. The next folder read takes its place. The config's scenario
  gives the window they are read with, and nothing else.
- **One thing at a time.** A read is a run of the button's job
  (`check.JOB`): one runs at a time, Stop stops it, and the start page shows
  it and how it ended the way it shows a check.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any, Callable

from . import check, job, project_setup, ready, state, when
from .words import counted_in_words as _counted

SUFFIXES = (".perfetto-trace", ".pftrace", ".trace")
SAID_SUFFIXES = "`.perfetto-trace`, `.pftrace` or `.trace`"
# Where the report of recordings from elsewhere goes, from the project's folder.
OUT = Path(".echolot") / "out" / "elsewhere"
# The longest path a form may name, in characters: one in another script,
# encoded in a form, still fits in what a request may carry (`ui.BODY_CAP`).
PATH_CAP = 512
# The most entries of one folder looked at, and the most folders inside it
# counted: a home folder named by mistake is answered at once.
LISTED = 5000
INSIDE = 40

# The names `collect` gives (`coldStart_iter002.perfetto-trace`) and the ones
# a macrobenchmark gives (`StartupBenchmark_startup_iter002_2026-11-20-140212
# .perfetto-trace`, and `.trace` from its older releases): what is before
# `_iter` names the scenario or the test.
_NAME = re.compile(r"^(?P<name>.+?)_iter(?P<i>\d+)(?:_(?P<date>\d{4}-\d{2}-\d{2}-\d{6}))?"
                   r"\.(?:perfetto-trace|pftrace|trace)$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _size(n: int) -> str:
    """A size as a person reads it: 640 KB, 23 MB, 1.4 GB."""
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} MB"
    return f"{max(1, round(n / 1024))} KB"


def is_recording(name: str) -> bool:
    return name.endswith(SUFFIXES) and not name.startswith(".")


# ---------------------------------------------------------------------------
# the folder a form names


def asked(project: Path, form: dict[str, str]) -> Path | None:
    """The folder a form names, as a path; None when it names none. Quotes a
    terminal would need around it are taken off, `~` is the home folder, and
    a relative path is taken from the project's folder. Raises
    `check.Refusal` for text that is no path: too long, or with a control
    character in it."""
    text = (form.get("folder") or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    if not text:
        return None
    if len(text) > PATH_CAP:
        raise check.Refusal("That is not a folder", f"A folder's path is {PATH_CAP} characters at most. Go back "
                            "and name the folder the recordings are in.", 400)
    if _CONTROL.search(text):
        raise check.Refusal("That is not a folder", "A path has no line breaks or control characters in it. Go "
                            "back and name the folder the recordings are in.", 400)
    path = Path(os.path.expanduser(text))
    return Path(os.path.abspath(project / path))


def shown(project: Path, path: Path) -> str:
    """A path as the page says it: from the project's folder when it is inside
    it, from the home folder when it is there, else whole."""
    for base, lead in ((project, ""), (Path.home(), "~/")):
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        return (lead + rel.as_posix()) if rel.parts else (lead.rstrip("/") or ".")
    return str(path)


@dataclass
class Group:
    """Recordings of one folder whose names say the same scenario or test:
    `name` is what comes before `_iter`, None for names that say none.
    `test` is a macrobenchmark's (its names carry a date), and `repeats`
    the iteration numbers that are there more than once."""
    name: str | None
    test: bool = False
    files: list[Path] = field(default_factory=list)
    size: int = 0
    newest: float = 0.0
    repeats: list[int] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.name or ""


@dataclass
class Found:
    """What a folder holds, as the page says it: `state` is `found`,
    `missing`, `file` (a file that is no recording), `empty` or `closed`
    (one that cannot be opened, `why` says how)."""
    path: Path
    shown: str
    state: str
    groups: list[Group] = field(default_factory=list)
    inside: list[tuple[str, Path, int]] = field(default_factory=list)
    why: str = ""
    cut: bool = False

    @property
    def count(self) -> int:
        return sum(len(g.files) for g in self.groups)


def _groups(files: list[tuple[Path, int, float]]) -> list[Group]:
    """The recordings by what their names say, the newest group first."""
    by: dict[str, Group] = {}
    seen: dict[str, list[int]] = {}
    for path, size, mtime in sorted(files, key=lambda f: f[0].name):
        m = _NAME.match(path.name)
        name = m.group("name") if m else None
        group = by.setdefault(name or "", Group(name=name, test=bool(m and m.group("date"))))
        group.test = group.test or bool(m and m.group("date"))
        group.files.append(path)
        group.size += size
        group.newest = max(group.newest, mtime)
        if m:
            seen.setdefault(name or "", []).append(int(m.group("i")))
    for key, numbers in seen.items():
        by[key].repeats = sorted({i for i in numbers if numbers.count(i) > 1})
    # Named ones before the ones whose names say nothing, the newest first.
    return sorted(by.values(), key=lambda g: (g.name is None, -g.newest))


def _recordings_in(folder: Path) -> tuple[list[tuple[Path, int, float]], list[Path], bool]:
    """The recordings in one folder, with their sizes and times; the folders
    in it; whether there was more than `LISTED` to look at. Raises OSError."""
    found, dirs = [], []
    with os.scandir(folder) as entries:
        listed = list(islice(entries, LISTED + 1))
    for entry in listed[:LISTED]:
        try:
            if entry.is_dir():
                if not entry.name.startswith("."):
                    dirs.append(Path(entry.path))
            elif entry.is_file() and is_recording(entry.name):
                st = entry.stat()
                found.append((Path(entry.path), st.st_size, st.st_mtime))
        except OSError:
            continue
    return found, sorted(dirs), len(listed) > LISTED


def look(project: Path, path: Path) -> Found:
    """What a folder holds, one level deep, by the names, sizes and times of
    its files: nothing is opened. A recording named by itself is a folder of
    one. Where the folder holds none, the folders in it that do are counted,
    so that the page can offer them."""
    out = Found(path=path, shown=shown(project, path), state="found")
    try:
        if path.is_file():
            if not is_recording(path.name):
                out.state = "file"
                return out
            st = path.stat()
            out.groups = _groups([(path, st.st_size, st.st_mtime)])
            return out
        files, dirs, out.cut = _recordings_in(path)
    except FileNotFoundError:
        out.state = "missing"
        return out
    except NotADirectoryError:
        out.state = "missing"
        return out
    except OSError as e:
        out.state, out.why = "closed", e.strerror or str(e)
        return out
    out.groups = _groups(files)
    if not out.groups:
        out.state = "empty"
        for sub in dirs[:INSIDE]:
            try:
                inner, _, _ = _recordings_in(sub)
            except OSError:
                continue
            if inner:
                out.inside.append((sub.name, sub, len(inner)))
    return out


# ---------------------------------------------------------------------------
# what the page says of it


def group_title(group: Group) -> str:
    if group.name is None:
        return "Names that say no scenario"
    return group.name


def group_text(group: Group, scenarios: list[str], each_s: float | None = None) -> str:
    """What one group is, in a sentence or two; with how long reading it
    takes where `each_s` says what one recording takes."""
    n = len(group.files)
    made = ready._ago(group.newest)
    count = _counted(n, "recording", "recordings")
    head = f"{count[0].upper() + count[1:]}, {_size(group.size)}" + (f", the newest made {made}" if made
                                                                      else "") + "."
    if group.name is None:
        what = "Their names do not say which scenario they are."
    elif group.test:
        what = f"A macrobenchmark's: the test `{group.name}`."
    elif group.name in scenarios:
        what = f"Named by `echolot collect` after the scenario `{group.name}` of your config."
    else:
        what = f"Named after the scenario `{group.name}`, which your config does not name."
    text = f"{head} {what}"
    if group.repeats:
        first = group.repeats[0]
        text += (f" The names repeat (iteration {first} is there more than once): these are recordings of more "
                 "than one sitting, and one report of them all mixes the sittings into one median. A folder that "
                 "holds one sitting reads as one.")
    if each_s is not None:
        text += f" Reading {'it' if n == 1 else 'them'} takes {check.about(n * each_s)}."
    return text


def summary(found: Found) -> str:
    """The page's first sentence about a folder."""
    where = f"`{found.shown}`"
    if found.state == "missing":
        return f"There is no folder {where}. Check the path, and look again."
    if found.state == "file":
        return f"{where} is a file, and no recording: echolot reads files whose names end in {SAID_SUFFIXES}."
    if found.state == "closed":
        return f"echolot cannot open {where}: {found.why}."
    if found.state == "empty":
        text = (f"echolot found no recordings in {where}. It reads the files whose names end in {SAID_SUFFIXES}, "
                "in the folder itself and not in the folders inside it.")
        if found.inside:
            text += " Folders inside it hold some: name the one to read."
        return text
    n, groups = found.count, found.groups
    if len(groups) == 1 and groups[0].name is not None:
        kind = "the test" if groups[0].test else "the scenario"
        text = f"echolot found {_counted(n, 'recording', 'recordings')} of {kind} `{groups[0].name}` in {where}."
    elif len(groups) == 1:
        text = f"echolot found {_counted(n, 'recording', 'recordings')} in {where}."
    else:
        text = (f"echolot found {n} recordings in {where}, of {len(groups)} kinds by their names. A report is of "
                "one kind: choose which to read.")
    if found.cut:
        text += f" The folder holds more than {LISTED} entries, and only the first {LISTED} were looked at."
    return text


def chosen_group(found: Found, scenarios: list[str], key: str | None = None) -> Group | None:
    """The group a press is about: the one named, else the one named after a
    scenario of the config, else the first."""
    if key is not None:
        return next((g for g in found.groups if g.key == key), None)
    return next((g for g in found.groups if g.name in scenarios), found.groups[0] if found.groups else None)


def chosen_scenario(scenarios: list[str], focus: str | None, group: Group | None) -> str | None:
    """The scenario whose window a group is read with: the one it is named
    after, else the one the page is about."""
    if group is not None and group.name in scenarios:
        return group.name
    return focus if focus in scenarios else (scenarios[0] if scenarios else None)


def scenarios_of(st: dict[str, Any]) -> tuple[list[str], str | None]:
    """The config's scenarios by name, and the one the page is about."""
    known = st.get("config") or {}
    if not known or known.get("error"):
        return [], None
    names = [sc["name"] for sc in st.get("scenarios") or []] or ([known["scenario"]] if known.get("scenario") else [])
    return names, known.get("scenario")


def command(project: Path, config: str, found: Found, group: Group, scenario: str | None,
            several: bool) -> dict[str, Any]:
    """The one command a press runs, as the job runs it and as a person would
    type it in the project's folder."""
    named = ["--scenario", scenario] if several and scenario else []
    out = project / OUT
    argv = [sys.executable, "-m", "echolot", "analyze", *[str(p) for p in group.files], "-c", config, *named,
            "--html", "-o", str(out)]
    if group.name is not None and len(group.files) > 1:
        suffixes = {next(s for s in SUFFIXES if p.name.endswith(s)) for p in group.files}
        tail = suffixes.pop() if len(suffixes) == 1 else "*"
        files = [f"{found.shown.rstrip('/')}/{group.name}_iter*{tail}"]
    elif len(group.files) <= 3:
        files = [shown(project, p) for p in group.files]
    else:
        files = [shown(project, p) for p in group.files[:2]] + [f"… ({len(group.files)} files)"]
    said = " ".join(["echolot", "analyze", *[f if "*" in f or "…" in f else shlex.quote(f) for f in files],
                     shlex.join(["-c", Path(config).name, *named, "--html", "-o", OUT.as_posix()])])
    return {"id": "analyze", "argv": argv, "shown": said}




# ---------------------------------------------------------------------------
# a press of Read them


def picked(form: dict[str, str]) -> tuple[str, str | None]:
    """The group and the scenario a press asks for, as the form sent them."""
    group = form.get("group")
    if group is None or len(group) > PATH_CAP or _CONTROL.search(group):
        raise check.Refusal("This press cannot be read", "The page sent something echolot does not know. Go back "
                            "to the start page and look in the folder again.", 400)
    scenario = (form.get("scenario") or "").strip() or None
    return group, scenario


def start(project: Path, config: str, st: dict[str, Any], form: dict[str, str], *,
          spawn: Callable[[Path, str, dict[str, Any]], Any] = job._spawn) -> dict[str, Any]:
    """Read the recordings a press chose, or raise `check.Refusal` with the
    reason. Held against the folder and the project as they are now: the
    folder is looked in again, and the group has to be there still."""
    if project_setup.busy(project):
        raise check.Refusal("Setting up is still under way", "echolot is checking this computer, which takes about "
                            "a minute. Read the recordings once it is done.", 409)
    run = job.read(project, check.JOB)
    if run is not None and run["status"] == job.RUNNING:
        raise check.Refusal("A check is already running", f"{check.running_words(run, state.collect_state(project))} "
                            "This press started nothing: one runs at a time. The start page shows how far it is.")
    why = check._stops_all(st, run)
    if why:
        raise check.Refusal("The recordings cannot be read now", why)
    path = asked(project, form)
    if path is None:
        raise check.Refusal("No folder named", "Name the folder the recordings are in, and look in it first.", 400)
    key, scenario = picked(form)
    found = look(project, path)
    if found.state != "found":
        raise check.Refusal("Nothing to read there", summary(found), 409)
    scenarios, focus = scenarios_of(st)
    group = chosen_group(found, scenarios, key)
    if group is None:
        raise check.Refusal("Nothing to read there", f"`{found.shown}` holds no recordings named so any more. Look "
                            "in it again: the page says what is there now.", 409)
    if scenario is None:
        scenario = chosen_scenario(scenarios, focus, group)
    if scenario not in scenarios:
        raise check.Refusal("No such scenario", "The config names no scenario by that name. Look in the folder "
                            "again and press Read them there.", 400)
    step = command(project, str((st.get("config") or {}).get("path") or config), found, group, scenario,
                   len(scenarios) > 1)
    what = {"do": "elsewhere", "scenario": scenario, "mode": None, "folder": found.shown,
            "count": len(group.files), "report": str(project / OUT / "report.json"), "before": None}
    try:
        return job.start(project, check.JOB, [step], what, spawn=spawn)
    except job.Refused as e:
        raise check.Refusal("The recordings were not read", f"echolot could not start it: {e}.", 500) from None


# ---------------------------------------------------------------------------
# the report of its own


def report_path(project: Path) -> Path:
    return project / OUT / "report.json"


def latest(project: Path) -> dict[str, Any] | None:
    """The report of recordings from elsewhere, for the start page's list:
    its verdict, where the recordings came from, and when it was made. None
    when there is none."""
    path = report_path(project)
    if not path.is_file():
        return None
    report = ready._report(path)
    if report is None:
        return {"path": path, "verdict": None, "plain": "A report of recordings from elsewhere is there and does "
                "not read. Read the folder again to write it anew."}
    traces = [str(t) for t in report.get("traces") or [] if isinstance(t, str)]
    folders = {str(Path(t).parent) for t in traces}
    where = f" in `{shown(project, Path(folders.pop()))}`" if len(folders) == 1 else ""
    made = ready._ago(when.iso_epoch(report.get("generated_at"))) or ready._ago(path.stat().st_mtime)
    runs = len(traces) or 1
    plain = (f"Made {made} from {_counted(runs, 'recording', 'recordings')}{where}, with the window of "
             f"`{report.get('scenario') or 'the scenario'}`. A report of its own: your scenarios' reports stay as "
             "they were.") if made else "A report of its own: your scenarios' reports stay as they were."
    return {"path": path, "verdict": report["verdict"], "plain": plain}
