"""The time limit, set or changed from `echolot ui` after the project was set
up: the one line of `echolot.yml` the page wrote for it, rewritten at a press.

`echolot ui` writes the limit when it sets a project up (`project_setup`):
`budget_ms` under `scenario:`, a line with a comment of the page's own, or the
same key commented out where no limit was given. A newcomer usually knows the
number only once the first report says "No time limit is set", so the page
may change that line later: exactly that line, and only while it still reads
as the page wrote it. A config written by hand or by an agent is never
touched, and no YAML is written: one line is matched and replaced as text.

What the file must be for its line to be rewritten, all of it:

- `echolot.yml` in the project's folder, the config this server reads, a
  file and no link to one, in UTF-8, loading as every command loads it, and
  beginning with the lines the page puts at its top (`project_setup.HEADER`);
- one scenario, under `scenario:`, and no limit in `local.yml` beside it,
  which would win over the line;
- exactly one line of the page's, under `scenario:`, which is one of

      `  budget_ms: <n>  # the time limit, <x.y> s: change the number to change it`
      `  # budget_ms: <n>  # a time limit in milliseconds: take the first `#` off, …`
      `  budget_ms: <n>  # a time limit in milliseconds: take the first `#` off, …`

  to the byte: the key, the two spaces before it, the spacing around the
  number and the comment as the page writes them, a whole number of
  milliseconds, and nothing after the comment but the line's end (`\\n`, or
  `\\r\\n`, which is kept). The number may be another than the page wrote and
  the first `#` may be gone, because the comments ask a person to do exactly
  that; the seconds in the first comment may then be stale, and a press
  writes them anew;
- and the limit YAML reads from the file is the one that line says: no other
  line sets it as well.

Anything else the page leaves to the person: it says why in a sentence and
shows the line to edit. A press rewrites that line and nothing else; every
other byte of the file stays. The new file is written beside the old one,
read back by the loader every command uses, and put in its place in one
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
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from . import check, job, plain, project_setup
from .config import Config, ConfigError

CONFIG = project_setup.CONFIG
LOCAL = "local.yml"
Refusal = check.Refusal

# Where a press sends the browser back to: a page's name, never an address.
BACK = ("report", "start")
_HASH = re.compile(r"[0-9a-f]{64}")
_NUMBER = "(?P<ms>[1-9][0-9]{0,5})"
# The seconds in the first comment, as `plain.hum` writes a limit the field takes.
_WORDS = r"[0-9]{1,2}\.[0-9] s"


def _patterns() -> tuple[re.Pattern[str], re.Pattern[str]]:
    """The page's two lines as patterns, from the very templates it writes them
    by: every word as it is, the number a whole one."""
    head, rest = project_setup.LIMIT_SET.split("{ms}")
    mid, tail = rest.split("{words}")
    on = re.compile(re.escape(head) + _NUMBER + re.escape(mid) + _WORDS + re.escape(tail))
    head, tail = project_setup.LIMIT_OFF.split("{ms}")
    # The same line with its first `#` taken off, as its comment says to.
    taken = head.replace("# ", "", 1)
    off = re.compile(f"(?:{re.escape(head)}|(?P<on>{re.escape(taken)})){_NUMBER}{re.escape(tail)}")
    return on, off


_ON, _OFF = _patterns()

# Why the page leaves the file alone, a sentence for each reason.
WHY = {
    "other": "This page reads `{config}`, and it changes no file but the `echolot.yml` it wrote itself.",
    "link": "`echolot.yml` is a link to another file, and the page changes no file through a link.",
    "unreadable": "`echolot.yml` does not read as text, so the page changes nothing in it.",
    "broken": "`echolot.yml` does not load, so the page changes nothing in it: the start page says which line to fix.",
    "foreign": "The page did not write this `echolot.yml`, so it changes nothing in it: the file does not begin "
               "with the two lines the page puts at its top.",
    "several": "`echolot.yml` names several scenarios, so the page changes nothing in it: it sets the limit only "
               "in a file of one scenario that it wrote itself.",
    "local": "`local.yml`, beside `echolot.yml`, sets the limit too, and it wins over `echolot.yml`, so the page "
             "changes neither.",
    "removed": "The line the page wrote for the limit is no longer in `echolot.yml`, so the page changes nothing "
               "in the file.",
    "edited": "The line the page wrote for the limit was changed by hand, so the page leaves the file to you.",
    "moved": "The line the page wrote for the limit is no longer under `scenario:`, so the page leaves the file "
             "to you.",
    "twice": "Another line of `echolot.yml` sets the limit too, and that one counts, so the page changes nothing "
             "in the file.",
}


# ---------------------------------------------------------------------------
# the file, as the page sees it


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(text: str) -> list[tuple[int, int, str]]:
    """Every line as `(start, end, words)`: `end` is where its words stop, before
    the `\\n` or the `\\r\\n` that ends it, which a rewrite keeps."""
    out, start = [], 0
    while start < len(text):
        stop = text.find("\n", start)
        stop = len(text) if stop < 0 else stop
        end = stop - 1 if stop > start and text[stop - 1] == "\r" else stop
        out.append((start, end, text[start:end]))
        start = stop + 1
    return out


def _block(lines: list[tuple[int, int, str]], i: int) -> str:
    """The top-level line a line stands under: the nearest one above it that is
    not blank, not indented and no comment."""
    for _, _, words in reversed(lines[:i]):
        if words.strip() and words[0] not in " \t#":
            return words
    return ""


def _page_line(words: str) -> tuple[int, bool] | None:
    """`(milliseconds, set)` for a line that reads as the page wrote it, None for
    any other: `set` is whether the line holds a limit or is the commented key."""
    m = _ON.fullmatch(words)
    if m:
        return int(m["ms"]), True
    m = _OFF.fullmatch(words)
    if m:
        return int(m["ms"]), bool(m["on"])
    return None


def _no(reason: str, **say: str) -> dict[str, Any]:
    return {"can": False, "reason": reason, "why": WHY[reason].format(**say)}


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
    return raw.get("scenarios") is not None or (isinstance(scenario, dict) and "budget_ms" in scenario)


def reading(project: Path, config: str) -> dict[str, Any] | None:
    """`echolot.yml` as a press would find it: None where there is none, else
    whether its limit line may be rewritten (`can`), and either the line and
    the file's two fingerprints, or the reason it may not, in a sentence."""
    path = project / CONFIG
    if not _only_config(project, config):
        return _no("other", config=Path(config).name)
    if path.is_symlink():
        return _no("link")
    if not path.is_file():
        return None
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return _no("unreadable")
    # The page's first lines, whichever way the lines end: a checkout on
    # Windows may end every line of the file with `\r\n`.
    if not text.replace("\r\n", "\n").startswith(project_setup.HEADER):
        return _no("foreign")
    try:
        raw = yaml.safe_load(text)
        Config.load(path)
    except (yaml.YAMLError, ConfigError):
        return _no("broken")
    if not isinstance(raw, dict) or raw.get("scenarios") is not None:
        return _no("several")
    if not isinstance(raw.get("scenario"), dict):
        return _no("removed")
    if _local_limit(project):
        return _no("local")
    lines = _lines(text)
    found = [(i, said) for i, (_, _, words) in enumerate(lines) if (said := _page_line(words)) is not None]
    if not found:
        return _no("edited" if "budget_ms" in text else "removed")
    if len(found) > 1:
        return _no("edited")
    i, (ms, holds) = found[0]
    if not re.match(r"scenario:(\s|$)", _block(lines, i)):
        return _no("moved")
    scenario = raw["scenario"]
    if (scenario.get("budget_ms") != ms) if holds else ("budget_ms" in scenario):
        return _no("twice")
    start, end, _ = lines[i]
    return {"can": True, "ms": ms if holds else None, "text": text, "start": start, "end": end,
            "seen": _digest(text), "rest": _digest(text[:start] + text[end:])}


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
    with contextlib.suppress(OSError, ValueError):
        data = json.loads(Path(rep["path"]).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    return None


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
    reading (`can`, and the line or why not), the limit in force (`now`) and
    the field's text, the key it is under, and, where the report is one made
    with another limit than the config's now, `behind`. `report` is that
    report as `analyze` wrote it; the scenario's last one when none is given."""
    known = st.get("config")
    if not known or known.get("error"):
        return None
    found = reading(project, config)
    if found is None:
        return None
    try:
        cfg = Config.load(known["path"], scenario=scenario or known.get("scenario"))
        now = cfg.scenario_budget_ms
    except ConfigError:
        return None
    out = {k: v for k, v in found.items() if k not in ("text", "start", "end")}
    out.update(now=now, seconds=_seconds(found["ms"] if found["can"] else now), range=(project_setup.LIMIT_MIN_S,
               project_setup.LIMIT_MAX_S), under="scenario" if cfg.get("scenarios") is None else
               f"scenarios.{cfg.scenario_name}", behind=None)
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
    milliseconds or None, the file's two fingerprints as the page saw it, and
    the page to go back to. None of it is used as a path or written as YAML:
    the fingerprints are compared, and the number is written by the page's
    own template."""
    seen, rest = form.get("seen") or "", form.get("rest") or ""
    if not _HASH.fullmatch(seen) or not _HASH.fullmatch(rest):
        raise Refusal("This press cannot be read", "The page did not say which `echolot.yml` it saw. Go back, "
                      "load the page again and press Set the limit there. Nothing was written.", 400)
    try:
        ms = project_setup.limit_ms(form.get("limit") or "")
    except ValueError:
        low, high = project_setup.LIMIT_MIN_S, project_setup.LIMIT_MAX_S
        raise Refusal("That is not a time limit", f"The limit is a number of seconds from {low:g} to {high:g}, like 2 "
                      "or 2.5. Leave the field empty to take the limit off. Go back, put it right and press Set the "
                      "limit again. Nothing was written.", 400) from None
    back = form.get("back")
    return {"limit_ms": ms, "seen": seen, "rest": rest, "back": back if back in BACK else "start"}


def _edit(found: dict[str, Any]) -> str:
    """Where the limit is to be changed by hand, for a sentence of refusal."""
    if found.get("reason") == "local":
        return "To change the limit, edit its line in `local.yml`."
    return ("To change the limit, edit `budget_ms` under `scenario:` in `echolot.yml`, in milliseconds: "
            "`budget_ms: 2000` is 2 seconds.")


def _reads_back(old: str, new: str, ms: int | None) -> bool:
    """Whether the new text says what the old one did, with that one limit."""
    try:
        before, after = yaml.safe_load(old), yaml.safe_load(new)
    except yaml.YAMLError:
        return False
    scenario = {k: v for k, v in before["scenario"].items() if k != "budget_ms"}
    if ms is not None:
        scenario["budget_ms"] = ms
    return after == {**before, "scenario": scenario}


def write(project: Path, config: str, press: dict[str, Any]) -> bool:
    """Set the limit a press asks for, or raise `Refusal` with the reason and
    that nothing was written. True when the file was rewritten, False when it
    already was as the press leaves it: the same press, sent twice."""
    found = reading(project, config)
    if found is None:
        raise Refusal("The limit was not changed", "There is no `echolot.yml` in this project now. Nothing was "
                      "written. Go back to the start page: it says what to do next.")
    if not found["can"]:
        raise Refusal("The limit was not changed", f"{found['why']} Nothing was written. {_edit(found)}")
    text, start, end = found["text"], found["start"], found["end"]
    line = project_setup.limit_line(press["limit_ms"])
    if found["seen"] != press["seen"]:
        if found["rest"] == press["rest"] and text[start:end] == line:
            return False
        raise Refusal("The limit was not changed", "`echolot.yml` changed after this page was drawn, and it may "
                      "hold what somebody wrote meanwhile. Nothing was written. Go back and load the page again: it "
                      "reads the file as it is now.")
    if text[start:end] == line:
        return False
    new = text[:start] + line + text[end:]
    if not _reads_back(text, new, press["limit_ms"]):
        raise Refusal("The limit was not changed", "`echolot.yml` would not read the way the page wrote it with "
                      f"that line changed. Nothing was written. {_edit(found)}")
    _replace(project / CONFIG, text, new, press["limit_ms"])
    return True


def _replace(path: Path, old: str, new: str, ms: int | None) -> None:
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
            loaded = Config.load(tmp).scenario_budget_ms
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
