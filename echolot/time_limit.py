"""The time limit, set or changed from `echolot ui` after the project was set
up: the one line of `echolot.yml` that holds `budget_ms`, rewritten at a press.

`echolot ui` writes the limit when it sets a project up (`project_setup`):
`budget_ms` under `scenario:`, a line with a comment of the page's own, or the
same key commented out where no limit was given. A newcomer usually knows the
number only once the first report says "No time limit is set", so the page
may change it later. Which file it may change is a matter of what the file
holds, not of who wrote it: a config written by hand, by `scan` or by an
agent gets the field as the page's own does (#818). No YAML is written: one
line is found by the positions YAML itself reads the file at, and changed as
text.

What the file must be for the page to change the limit in it:

- `echolot.yml` in the project's folder, the config this server reads, a
  file and no link to one, in UTF-8, loading as every command loads it;
- no limit in `local.yml` beside it, which would win over the line;
- the scenario the page is about written as a block of keys, under
  `scenario:` or as one of `scenarios:`, with its limit in one of these:

  - `budget_ms: <number>` on a line of the block. A press changes the number
    and keeps the rest of the line, a comment included; an empty field turns
    the line into a comment, `# budget_ms: <number>`, which the next press
    turns back. The page's own lines are the page's: a press writes them as
    the page wrote them, the seconds in their comment anew;
  - `budget_ms` commented out on a line of the block, as above or as the
    page writes it: a press with a number takes the `#` off;
  - no `budget_ms` at all: a press with a number adds the page's own line
    as the block's first, at the indentation of its keys;
  - `budget_ms: <number>` beside other keys on one line, `{budget_ms: 500}`:
    a press changes the number, and cannot take the limit off;

- and whatever the press writes, the file reads back as it read before with
  that one limit changed: every other key, of every other scenario too.

Anything else the page leaves to the person: it says why in a sentence and
names the line to edit. A press rewrites that line or adds that one line,
and nothing else; every other byte of the file stays, comments, blank lines
and the order of the keys included. The new file is written beside the old
one, read back by the loader every command uses, and put in its place in one
step, so a reader never sees half a file.

The press carries two fingerprints of the file as the page saw it: all of
it, and all of it but that line. A file that is as the page saw it is
rewritten; one that is already as this press leaves it is the same press
sent twice and is answered as the first was; anything else changed between
the page and the press, and is left as it is.

`analyze` reads the limit when it reads the recordings, so a report keeps
the limit it was made with until they are read again. The page says so and
offers to read them again: the button's own `read` (`echolot/check.py`),
which needs no phone.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode

from . import check, files, job, plain, project_setup
from .config import Config, ConfigError

CONFIG = project_setup.CONFIG
LOCAL = "local.yml"
KEY = "budget_ms"
Refusal = check.Refusal

# Where a press sends the browser back to: a page's name, never an address.
BACK = ("report", "start")
_HASH = re.compile(r"[0-9a-f]{64}")
# A scenario's name as a press sends it: compared with the config's, never
# used as a path.
_NAME = re.compile(r"[^\x00-\x1f/]{1,200}")
_NUMBER = "(?P<ms>[1-9][0-9]{0,5})"
# The seconds in the first comment, as `plain.hum_limit` writes a limit the
# field takes: up to three decimals, and one in a file written before (#868).
_WORDS = r"[0-9]{1,2}\.[0-9]{1,3} s"
# A line that holds the limit or held it, written by anybody: the key, a
# number, and a comment or nothing after it. The `#` before the key is a
# limit taken off.
_HELD = re.compile(r"(?P<indent>[ \t]*)(?P<off>#[ \t]*)?budget_ms:(?P<gap>[ \t]+)(?P<ms>[0-9][0-9_.eE+]*)"
                   r"(?P<tail>(?:[ \t]+#.*)?[ \t]*)")


def _patterns() -> tuple[re.Pattern[str], re.Pattern[str]]:
    """The page's two lines as patterns, from the very templates it writes them
    by, at any indentation: every word as it is, the number a whole one."""
    head, rest = project_setup.LIMIT_SET.lstrip(" ").split("{ms}")
    mid, tail = rest.split("{words}")
    on = re.compile(r"(?P<indent>[ \t]*)" + re.escape(head) + _NUMBER + re.escape(mid) + _WORDS + re.escape(tail))
    head, tail = project_setup.LIMIT_OFF.lstrip(" ").split("{ms}")
    # The same line with its first `#` taken off, as its comment says to.
    taken = head.replace("# ", "", 1)
    off = re.compile(r"(?P<indent>[ \t]*)" + f"(?:{re.escape(head)}|(?P<on>{re.escape(taken)})){_NUMBER}"
                     f"{re.escape(tail)}")
    return on, off


_ON, _OFF = _patterns()

# Why the page leaves the file alone, a sentence for each reason.
WHY = {
    "other": "This page reads `{config}`, and it changes no file but `echolot.yml`.",
    "link": "`echolot.yml` is a link to another file, or has another name as well, and the page changes no file "
            "through a link.",
    "unreadable": "`echolot.yml` does not read as text, so the page changes nothing in it.",
    "broken": "`echolot.yml` does not load, so the page changes nothing in it: the start page says which line to fix.",
    "gone": "`echolot.yml` names no scenario `{name}` now, so the page changes nothing in it.",
    "local": "`local.yml`, beside `echolot.yml`, sets the limit too, and it wins over `echolot.yml`, so the page "
             "changes neither.",
    "shape": "`{under}:` in `echolot.yml` is not written as a block of keys with the limit on a line of its own, "
             "so the page leaves the file to you.",
}


# ---------------------------------------------------------------------------
# the file, as the page sees it


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(text: str) -> list[tuple[int, int, int, str]]:
    """Every line as `(start, end, stop, words)`: `end` is where its words stop,
    before the `\\n` or the `\\r\\n` that ends it, which a rewrite keeps, and
    `stop` is where the next line starts."""
    out, start = [], 0
    while start < len(text):
        stop = text.find("\n", start)
        stop = len(text) if stop < 0 else stop
        end = stop - 1 if stop > start and text[stop - 1] == "\r" else stop
        out.append((start, end, min(stop + 1, len(text)), text[start:end]))
        start = stop + 1
    return out


def _no(reason: str, line: int | None = None, **say: str) -> dict[str, Any]:
    return {"can": False, "reason": reason, "why": WHY[reason].format(**say), "line": line}


def _only_config(project: Path, config: str) -> bool:
    return project_setup._only_config(config, project)


def _local_limit(project: Path) -> bool:
    """Whether `local.yml` beside the config says anything of the limit: its
    `scenario.budget_ms`, or scenarios of its own."""
    path = project / LOCAL
    if not path.exists():
        return False
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return True
    if not isinstance(raw, dict):
        return False
    scenario = raw.get("scenario")
    return raw.get("scenarios") is not None or (isinstance(scenario, dict) and KEY in scenario)


def _pair(node: Any, key: str) -> tuple[Any, Any]:
    """The key node and the value node of `key` in a mapping node, or Nones."""
    if not isinstance(node, MappingNode):
        return None, None
    return next(((k, v) for k, v in node.value if isinstance(k, ScalarNode) and k.value == key), (None, None))


def _with(data: Any, keys: tuple[str, ...], ms: int | None) -> Any:
    """What the file should read as with the limit of the block at `keys` set
    to `ms` (taken off for None), and nothing else changed. Copied along the
    way down only: a block another key shares by an anchor is not the one
    changed, and the comparison then fails, as it should."""
    if not keys:
        out = {k: v for k, v in data.items() if k != KEY}
        if ms is not None:
            out[KEY] = ms
        return out
    return {**data, keys[0]: _with(data[keys[0]], keys[1:], ms)}


def _reads_back(old: str, new: str, keys: tuple[str, ...], ms: int | None) -> bool:
    """Whether the new text says what the old one did, with that one limit."""
    try:
        before, after = yaml.safe_load(old), yaml.safe_load(new)
        return after == _with(before, keys, ms)
    except (yaml.YAMLError, KeyError, AttributeError, TypeError):
        return False


def _span(m: re.Match[str], start: int) -> tuple[int, int]:
    """Where a line's number is in the whole text."""
    return start + m.start("ms"), start + m.end("ms")


def written(found: dict[str, Any], ms: int | None) -> str | None:
    """What a press that sets `ms` (None: takes the limit off) puts in place of
    `text[start:end]`, or None where it cannot take the limit off. The page's
    own line is written as the page writes it; anybody else's keeps all but
    its number, or gets a `#` before its key."""
    kind, words = found["kind"], found.get("words", "")
    if kind == "page":
        return found["indent"] + project_setup.limit_line(ms).lstrip(" ")
    if kind == "insert":
        return "" if ms is None else found["indent"] + project_setup.limit_line(ms).lstrip(" ") + found["eol"]
    if kind == "value":
        return None if ms is None else str(ms)
    m = _HELD.fullmatch(words)
    if ms is None:
        return words if m["off"] else f"{m['indent']}# {KEY}:{m['gap']}{m['ms']}{m['tail']}"
    return f"{m['indent']}{KEY}:{m['gap']}{ms}{m['tail']}"


def _found(text: str, keys: tuple[str, ...], kind: str, start: int, end: int, cut: tuple[int, int], line: int,
           **more: Any) -> dict[str, Any] | None:
    """The place a press writes, once a press there reads back as it should:
    a number in it, and, where the line can say so, no limit."""
    found = {"kind": kind, "start": start, "end": end, "line": line, **more}
    for probe in (1000, 2000):
        piece = written(found, probe)
        if piece is None or not _reads_back(text, text[:start] + piece + text[end:], keys, probe):
            return None
    off = written(found, None)
    if off is not None and not _reads_back(text, text[:start] + off + text[end:], keys, None):
        return None
    found["rest"] = _digest(text[:cut[0]] + text[cut[1]:])
    found["off"] = off is not None
    return found


def _place(text: str, keys: tuple[str, ...]) -> tuple[dict[str, Any] | None, int | None]:
    """Where in the text the limit of the block at `keys` is, or would go: the
    place a press writes, or None and the line a person would edit."""
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return None, None
    block = root
    for key in keys:
        _, block = _pair(block, key)
    if not isinstance(block, MappingNode):
        return None, None
    lines = _lines(text)
    name, value = _pair(block, KEY)
    if value is not None:
        # The number on the key's own line, as it is written there: not one
        # on the line below, not a value an anchor elsewhere holds.
        at = name.start_mark.line
        if (not isinstance(value, ScalarNode) or value.style is not None or value.start_mark.line != at
                or value.end_mark.line != at or value.start_mark.index < name.end_mark.index):
            return None, at + 1
        start, end, stop, words = lines[at]
        span = (value.start_mark.index, value.end_mark.index)
        page = _ON.fullmatch(words) or _OFF.fullmatch(words)
        held = _HELD.fullmatch(words)
        if page is not None and (page.re is _ON or page["on"]) and _span(page, start) == span:
            found = _found(text, keys, "page", start, end, (start, stop), at + 1, indent=page["indent"])
        elif held is not None and not held["off"] and _span(held, start) == span:
            found = _found(text, keys, "line", start, end, (start, stop), at + 1, words=words)
        else:
            found = _found(text, keys, "value", *span, span, at + 1)
        return found, at + 1
    # No limit: a line that held one, commented out, in this block, the
    # page's or anybody's; else a line of the page's own, added.
    for at, (start, end, stop, words) in enumerate(lines):
        page, held = _OFF.fullmatch(words), _HELD.fullmatch(words)
        if page is not None and not page["on"]:
            found = _found(text, keys, "page", start, end, (start, stop), at + 1, indent=page["indent"])
        elif held is not None and held["off"]:
            found = _found(text, keys, "line", start, end, (start, stop), at + 1, words=words)
        else:
            continue
        if found is not None:
            return found, at + 1
    if block.flow_style or not block.value:
        return None, block.start_mark.line + 1
    first = block.value[0][0].start_mark
    start, end, stop, words = lines[first.line]
    if words[:first.column].strip():
        return None, first.line + 1
    eol = "\r\n" if text[end:end + 2] == "\r\n" else "\n"
    return (_found(text, keys, "insert", start, start, (start, start), first.line + 1, indent=words[:first.column],
                   eol=eol), first.line + 1)


def reading(project: Path, config: str, scenario: str | None = None) -> dict[str, Any] | None:
    """`echolot.yml` as a press would find it: None where there is none, else
    whether the limit of the scenario named (the config's first when none
    is) may be set from the page (`can`), with where a press writes and the
    file's two fingerprints, or the reason it may not, in a sentence, and the
    line to edit where there is one."""
    path = project / CONFIG
    if not _only_config(project, config):
        return _no("other", config=Path(config).name)
    if path.is_symlink():
        return _no("link")
    if not path.is_file():
        return None
    # A file with a second name: putting a new file in its place would part
    # the two, and the other name would keep the old limit.
    with contextlib.suppress(OSError):
        if path.stat().st_nlink > 1:
            return _no("link")
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return _no("unreadable")
    try:
        raw = yaml.safe_load(text)
        cfg = Config.load(path)
    except (yaml.YAMLError, ConfigError):
        return _no("broken")
    if scenario is not None and scenario not in cfg.scenarios:
        return _no("gone", name=scenario)
    cfg.select(scenario)
    several = isinstance(raw, dict) and raw.get("scenarios") is not None
    keys = ("scenarios", cfg.scenario_name) if several else ("scenario",)
    under = ".".join(keys)
    if _local_limit(project):
        return {**_no("local"), "under": under}
    found, line = _place(text, keys)
    if found is None:
        return {**_no("shape", line, under=under), "under": under}
    return {"can": True, **found, "under": under, "scenario": cfg.scenario_name if several else None,
            "ms": cfg.scenario_budget_ms, "text": text, "seen": _digest(text)}


# ---------------------------------------------------------------------------
# what the page shows


def _seconds(ms: float | None) -> str:
    """A limit as the field shows it: seconds, and no more digits than it has."""
    return "" if ms is None else f"{ms / 1000:g}"


def last_report(st: dict[str, Any]) -> dict[str, Any] | None:
    """The scenario's last report, as `analyze` wrote it, or None."""
    rep = st.get("report")
    if not rep or rep.get("error"):
        return None
    data = files.read_report(Path(rep["path"]))
    return None if isinstance(data, files.Refusal) else dict(data)


def _again(project: Path, st: dict[str, Any], scenario: str | None) -> dict[str, Any]:
    """The press that reads the recordings again, as the button reads them:
    how many there are, the line beside it, and why it cannot run now."""
    run = job.read(project, check.JOB)
    found = check.offer(project, st, [], run)
    choice = check.chosen(found, scenario)
    if choice is None:
        return {"n": 0}
    n = choice.get("unread") or choice.get("there") or 0
    why = found.get("why")
    if run is not None and run["status"] == job.RUNNING:
        why = "Not now: a check is running. The start page shows how far it is."
    if choice.get("unread"):
        said = (f"{check._counted(n, 'recording waits', 'recordings wait')} to be read, newer than the report. "
                f"Reading {'it' if n == 1 else 'them'} takes {check.about(n * check.READ_S)}.")
    else:
        said = (f"The {check._counted(n, 'recording', 'recordings')} that {'is' if n == 1 else 'are'} here "
                f"{'is' if n == 1 else 'are'} read again: {check.about(n * check.READ_S)}.")
    return {"n": n, "what": f"{check.READ}:{choice['name']}", "why": why, "hint": f"{said} The phone is not needed."}


def view(project: Path, config: str, st: dict[str, Any], report: dict[str, Any] | None = None,
         scenario: str | None = None) -> dict[str, Any] | None:
    """What the page shows of the limit, for the scenario named (the config's
    own when none is): None where there is no config that loads, else the
    reading (`can`, and where a press writes or why not), the limit in force
    (`now`) and the field's text, the key it is under, the line it is on,
    and, where the report is one made with another limit than the config's
    now, `behind`. `report` is that report as `analyze` wrote it; the
    scenario's last one when none is given."""
    known = st.get("config")
    if not known or known.get("error"):
        return None
    try:
        cfg = Config.load(known["path"], scenario=scenario or known.get("scenario"))
        now = cfg.scenario_budget_ms
    except ConfigError:
        return None
    found = reading(project, config, scenario or known.get("scenario"))
    if found is None:
        return None
    out = {k: v for k, v in found.items() if k not in ("text", "start", "end", "words", "indent", "eol")}
    out.update(now=now, seconds=_seconds(now), range=(project_setup.LIMIT_MIN_S, project_setup.LIMIT_MAX_S),
               under=found.get("under") or ("scenario" if cfg.get("scenarios") is None
                                            else f"scenarios.{cfg.scenario_name}"), behind=None)
    report = report if report is not None else last_report(st)
    if report is not None:
        was = plain._budget(report)
        if was != now:
            out["behind"] = {"was": was, "now": now, **_again(project, st, cfg.scenario_name)}
    return out


# ---------------------------------------------------------------------------
# a press


def asked(form: dict[str, str]) -> dict[str, Any]:
    """What a press asks for, from the form it sent: the limit in whole
    milliseconds or None, the scenario it is of, the file's two fingerprints
    as the page saw it, and the page to go back to. None of it is used as a
    path or written as YAML: the fingerprints are compared, the scenario is
    one the config names, and the number is written as digits."""
    seen, rest = form.get("seen") or "", form.get("rest") or ""
    if not _HASH.fullmatch(seen) or not _HASH.fullmatch(rest):
        raise Refusal("This press cannot be read", "The page did not say which `echolot.yml` it saw. Go back, "
                      "load the page again and press Set the limit there. Nothing was written.", 400)
    scenario = form.get("scenario") or None
    if scenario is not None and not _NAME.fullmatch(scenario):
        raise Refusal("This press cannot be read", "The page named a scenario echolot cannot read. Go back, load "
                      "the page again and press Set the limit there. Nothing was written.", 400)
    try:
        ms = project_setup.limit_ms(form.get("limit") or "")
    except ValueError:
        low, high = project_setup.LIMIT_MIN_S, project_setup.LIMIT_MAX_S
        raise Refusal("That is not a time limit", f"The limit is a number of seconds from {low:g} to {high:g}, like 2 "
                      "or 2.5. Leave the field empty to take the limit off. Go back, put it right and press Set the "
                      "limit again. Nothing was written.", 400) from None
    back = form.get("back")
    return {"limit_ms": ms, "scenario": scenario, "seen": seen, "rest": rest,
            "back": back if back in BACK else "start"}


def _edit(found: dict[str, Any]) -> str:
    """Where the limit is to be changed by hand, for a sentence of refusal."""
    if found.get("reason") == "local":
        return "To change the limit, edit its line in `local.yml`."
    at = f", line {found['line']}" if found.get("line") else ""
    return (f"To change the limit, edit `budget_ms` under `{found.get('under') or 'scenario'}:` in "
            f"`echolot.yml`{at}, in milliseconds: `budget_ms: 2000` is 2 seconds.")


def write(project: Path, config: str, press: dict[str, Any]) -> bool:
    """Set the limit a press asks for, or raise `Refusal` with the reason and
    that nothing was written. True when the file was rewritten, False when it
    already was as the press leaves it: the same press, sent twice."""
    found = reading(project, config, press.get("scenario"))
    if found is None:
        raise Refusal("The limit was not changed", "There is no `echolot.yml` in this project now. Nothing was "
                      "written. Go back to the start page: it says what to do next.")
    if not found["can"]:
        raise Refusal("The limit was not changed", f"{found['why']} Nothing was written. {_edit(found)}")
    text, start, end = found["text"], found["start"], found["end"]
    piece = written(found, press["limit_ms"])
    if piece is None:
        raise Refusal("The limit was not changed", f"`budget_ms` shares line {found['line']} of `echolot.yml` with "
                      "other keys, and the page takes a limit off by turning its line into a comment. Nothing was "
                      f"written. To take the limit off, delete `budget_ms` there.")
    if found["seen"] != press["seen"]:
        if found["rest"] == press["rest"] and text[start:end] == piece:
            return False
        raise Refusal("The limit was not changed", "`echolot.yml` changed after this page was drawn, and it may "
                      "hold what somebody wrote meanwhile. Nothing was written. Go back and load the page again: it "
                      "reads the file as it is now.")
    if text[start:end] == piece:
        return False
    new = text[:start] + piece + text[end:]
    keys = tuple(found["under"].split(".", 1))
    if not _reads_back(text, new, keys, press["limit_ms"]):
        raise Refusal("The limit was not changed", "`echolot.yml` would not read the way the page wrote it with "
                      f"that line changed. Nothing was written. {_edit(found)}")
    _replace(project / CONFIG, text, new, press["limit_ms"], press.get("scenario"))
    return True


def _replace(path: Path, old: str, new: str, ms: int | None, scenario: str | None = None) -> None:
    """Put `new` in place of the file in one step: written beside it, loaded as
    every command loads a config, and moved over it only while the file still
    holds `old`."""
    fd, name = tempfile.mkstemp(prefix=".echolot.yml.", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(new.encode("utf-8"))
            out.flush()
            os.fsync(out.fileno())
        with contextlib.suppress(OSError):
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        try:
            loaded = Config.load(tmp, scenario=scenario).scenario_budget_ms
        except ConfigError as e:
            raise Refusal("The limit was not changed", f"With that line the config would not load: {e}. Nothing "
                          "was written.", 500) from None
        if loaded != ms:
            raise Refusal("The limit was not changed", "With that line the config would not read the limit asked "
                          "for. Nothing was written.", 500)
        if path.read_bytes() != old.encode("utf-8"):
            raise Refusal("The limit was not changed", "`echolot.yml` changed in the instant of the press. Nothing "
                          "was written. Go back and load the page again.")
        os.replace(tmp, path)
    except OSError as e:
        raise Refusal("echolot.yml could not be written", f"{e.strerror or e}. Nothing was changed.", 500) from None
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
