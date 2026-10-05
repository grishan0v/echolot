"""The reflect report itself: where its config comes from, what its files are
called, how it reads a brief, and how it prints.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder  # noqa: E402
from echolot.main import main  # noqa: E402
from echolot.reflect import cli as reflect_cli  # noqa: E402
from echolot.reflect import facts as facts_mod  # noqa: E402
from echolot.reflect import render as render_mod  # noqa: E402
from echolot.reflect.model import Session, SubAgent  # noqa: E402
from tests.support import check  # noqa: E402

GUIDE = (Path(__file__).resolve().parent.parent / "echolot" / "guide" / "hunt.md").read_text(
    encoding="utf-8")
TEMPLATE = GUIDE.split("```text\n", 1)[1].split("```", 1)[0]


def _log(project: Path) -> None:
    log = project / recorder.LOG_FILE
    log.parent.mkdir(parents=True)
    log.write_text("".join(json.dumps({"ts": f"2026-09-01T10:0{i}:00+00:00", "cmd": c,
                                       "argv": [c], "exit": 0, "ms": 100}) + "\n"
                           for i, c in enumerate(("doctor", "analyze"))), encoding="utf-8")


def _reflect(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["reflect", *argv])
    return code, out.getvalue() + err.getvalue()


def test_an_explicit_config_is_read_from_here(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "p1"
    _log(project)
    (project / "echolot.yml").write_text("loop: {max_rounds: 5}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, said = _reflect("--from-log", "--project", "p1", "-c", "p1/echolot.yml")
    written = sorted((project / ".echolot" / "reflect").glob("*.json"))
    context = json.loads(written[-1].read_text(encoding="utf-8"))["context"] if written else {}
    check("the config given is the config read",
          code == 0 and (context.get("config") or {}).get("present") is True, said[-600:])
    code, said = _reflect("--from-log", "--project", "p1", "-c", "nonexistent.yml")
    check("and one that is not there is said", "config ignored: config not found" in said,
          said[-600:])


def test_ids_that_share_a_minute_get_names_of_their_own() -> None:
    short = reflect_cli._short_ids(["019a0f19-d1c2-7a00-0000", "019a0f19-fe40-7b00-0000",
                                    "abcdef12-0000"])
    check("unique, and eight long where eight is enough",
          short["019a0f19-d1c2-7a00-0000"] != short["019a0f19-fe40-7b00-0000"]
          and short["abcdef12-0000"] == "abcdef12", short)


def test_a_filled_brief_names_the_change_and_an_unfilled_one_names_nothing() -> None:
    check("the template as printed names nothing",
          facts_mod._prompt_mentions(TEMPLATE) == {"traces": False, "regression": False,
                                                    "since_change": False},
          facts_mod._prompt_mentions(TEMPLATE))
    filled = (TEMPLATE
              .replace("<each trace file by name, from the directory `echolot hunt` set aside "
                       "or from .echolot/traces>",
                       ".echolot/traces/coldStart_iter000.perfetto-trace "
                       ".echolot/traces/coldStart_iter001.perfetto-trace")
              .replace('<what, against what>, after <the change, or "unknown">',
                       "cold start 1.9 s, was 1.2 s, after the tab redesign"))
    check("filled in as asked, all three are there",
          facts_mod._prompt_mentions(filled) == {"traces": True, "regression": True,
                                                  "since_change": True},
          facts_mod._prompt_mentions(filled))
    check("and `after unknown` is a change given",
          facts_mod._prompt_mentions("Regressed: scroll 20 ms, after unknown")["since_change"])


def test_a_brief_the_source_does_not_carry_is_not_judged() -> None:
    s = Session(id="c", agent="codex", carries=["calls"])
    s.subagents = [SubAgent(id="t", type="perf-hunter")]
    hunt = facts_mod.hunts(s, None)[0]
    check("no marks in the facts", hunt["prompt_mentions"] is None, hunt)
    out: list[str] = []
    render_mod._hunts({"hunts": [hunt]}, out)
    check("and none in the report", any("not carried by this source" in line for line in out)
          and not any("since_change ✗" in line for line in out), out)


def test_notes_once_and_pipes_escaped_once() -> None:
    row = render_mod._table([{"command": "echolot names x | grep AGENTTMP_", "n": 1}])
    check("one backslash", "x \\| grep" in row and "\\\\|" not in row, row)
    check("no Reader notes section", "_notes" not in [f.__name__ for f in render_mod._SECTIONS])
