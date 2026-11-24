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


def test_an_explicit_out_is_a_path_from_here(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "app"
    _log(project)
    here = tmp_path / "audit"
    here.mkdir()
    monkeypatch.chdir(here)
    code, said = _reflect("--from-log", "--project", str(project), "-o", "r-out")
    check("the reports land where reflect was run",
          code == 0 and sorted(p.suffix for p in (here / "r-out").glob("*")) == [".json", ".md"],
          said[-600:])
    check("and the project is left untouched", not (project / "r-out").exists())
    code, said = _reflect("--from-log", "--project", str(project))
    check("without -o they go under the project, as before",
          code == 0 and any((project / ".echolot" / "reflect").glob("*.json")), said[-600:])
    check("and not here", not (here / ".echolot").exists())


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


def test_a_session_that_ran_on_a_copy_is_judged_by_the_copy(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "app"
    (project / "work-a").mkdir(parents=True)
    (project / "echolot.yml").write_text(
        "scenarios:\n  coldStart: {}\n  listScroll: {}\n", encoding="utf-8")
    (project / "work-a" / "echolot.yml").write_text(
        "scenario: {name: copyA}\nloop: {max_rounds: 7}\n", encoding="utf-8")
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    cmd = "echolot analyze .echolot/traces/a.perfetto-trace -c work-a/echolot.yml"
    rows = [
        {"type": "user", "timestamp": "2026-09-01T10:00:00.000Z", "cwd": str(project),
         "message": {"role": "user", "content": "analyze the copy"}},
        {"type": "assistant", "timestamp": "2026-09-01T10:00:05.000Z", "cwd": str(project),
         "message": {"id": "m1", "role": "assistant", "content": [
             {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": cmd}}]}},
        {"type": "user", "timestamp": "2026-09-01T10:00:09.000Z", "cwd": str(project),
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
    ]
    (tdir / "s1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows),
                                   encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, said = _reflect("--last", "--full", "--project", str(project), "--transcripts", str(tdir))
    rep = json.loads((project / ".echolot" / "reflect" / "s1.json").read_text(
        encoding="utf-8")) if code == 0 else {}
    cfg = (rep.get("context") or {}).get("config") or {}
    check("the copy is the config read", str(cfg.get("path", "")).endswith(
        "work-a/echolot.yml") and cfg.get("max_rounds") == 7, said[-600:])
    check("and the header says so",
          "work-a/echolot.yml` (the copy this session ran with `-c`)" in said
          and "listScroll" not in said.split("Config:", 1)[-1].split("\n", 1)[0], said[:900])


def test_a_config_changed_since_the_session_is_said(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "p1"
    _log(project)
    (project / "echolot.yml").write_text("loop: {max_rounds: 5}\n", encoding="utf-8")
    log = project / recorder.LOG_FILE
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    for r in lines:
        r["cwd"] = str(project)
        if r["cmd"] == "analyze":
            r["config"] = {"path": "echolot.yml", "sha": "0123456789ab"}
    log.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    monkeypatch.chdir(project)
    code, said = _reflect("--from-log", "--full", "--project", str(project))
    check("the header says the file is not what the session read",
          code == 0 and "The file changed after this session ran" in said
          and "`0123456789ab`" in said, said[:900])
