"""Outside a terminal `analyze` prints the overview and `reflect` the summary;
`--full`, or a terminal, gets the whole report (#492).

An agent reads what a command prints into its context. `analyze` printed the
whole report there — on a large app's cold start 35 KB, 466 lines, in every
round of a hunt — and the layer then had it read the report again with
`echolot report`. The doctor check pins the default case; these pin the
rest: a report written elsewhere is named in every pointer, a terminal still
gets the whole report, no warning about the window is lost on the way, and
`reflect` of one session follows the same rule.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import main as main_mod  # noqa: E402
from echolot import recorder  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402

CONFIG = "project:\n  package: com.example.app\n  process: com.example.app\n" \
         "scenario:\n  name: coldStart\n"


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def _run(*argv: str, out: io.StringIO | None = None) -> tuple[int, str, str]:
    out, err = out or io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _project(tmp_path: Path, monkeypatch, marker_report) -> Path:
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    trace = tmp_path / "a.perfetto-trace"
    trace.write_bytes(b"a trace")
    monkeypatch.setattr(main_mod, "analyze_trace", lambda *a, **kw: copy.deepcopy(marker_report))
    monkeypatch.chdir(tmp_path)
    return trace


def test_a_report_written_elsewhere_is_named_in_every_pointer(tmp_path, monkeypatch,
                                                              marker_report):
    """`report` with no path reads the latest, and `-o out/r1` is not it: a
    pointer without the path would show the agent another report's rows."""
    trace = _project(tmp_path, monkeypatch, marker_report)
    code, piped, err = _run("analyze", str(trace), "-o", "out/r1")
    check("analyze exits 0", code == 0, err[-400:])
    check("the pointers name the report's path",
          "`echolot report out/r1/report.json --detector <id> --top 5`" in piped, piped[-600:])
    check("and nothing points at the latest report instead",
          "`echolot report --" not in piped, piped[-600:])
    code, shown, err = _run("report", "out/r1/report.json")
    check("report of that path exits 0", code == 0, err[-400:])
    check("and prints what analyze printed, word for word", piped.startswith(shown),
          f"{piped[:400]}\n---\n{shown[:400]}")


def test_a_scenario_of_several_is_named_by_its_path_too(tmp_path, monkeypatch, marker_report):
    """With `scenarios:` `report` picks the open investigation's or the first,
    which need not be the one just measured."""
    trace = _project(tmp_path, monkeypatch, marker_report)
    (tmp_path / "echolot.yml").write_text(
        "project:\n  process: com.example.app\nscenarios:\n"
        "  coldStart: {}\n  feedScroll: {}\n", encoding="utf-8")
    code, piped, err = _run("analyze", str(trace), "--scenario", "feedScroll")
    check("analyze exits 0", code == 0, err[-400:])
    check("the pointers name the scenario's report",
          "`echolot report .echolot/out/feedScroll/report.json --detector" in piped,
          piped[-600:])


def test_a_terminal_gets_the_whole_report(tmp_path, monkeypatch, marker_report):
    trace = _project(tmp_path, monkeypatch, marker_report)
    code, shown, err = _run("analyze", str(trace), out=Terminal())
    check("analyze exits 0", code == 0, err[-400:])
    written = json.loads((tmp_path / ".echolot/out/report.json").read_text(encoding="utf-8"))
    check("a terminal gets the whole report, as before",
          shown == report_mod.to_markdown(written) + "\n", shown[:300])
    check("and its paths stay on stderr", "report.md" in err and "report.md" not in shown, err)


def test_the_overview_keeps_every_warning_about_the_window(marker_report):
    """Above its findings the whole report says when its numbers are not about
    the scenario. Outside a terminal the overview is all an agent sees, so it
    has to say the same: a set whose repeats diverged, an anchor one repeat
    missed, repeats of different processes, a window that opened inside a
    wait, a recording given twice."""
    rep = copy.deepcopy(marker_report)
    w = rep["window"]
    w.update(duration_ms_min=100.0, duration_ms_max=450.0,
             processes=["com.example.app", "com.example.app:remote"],
             opened_inside={"material": True, "state": "S", "total_ms": 80.0,
                            "before_ms": 30.0})
    w["start_anchor"] = {**(w.get("start_anchor") or {}), "glob": "AppStart",
                         "matches": 1, "missed": "2/5"}
    rep["same_recording"] = [["a.perfetto-trace", "b.perfetto-trace"]]
    whole, overview = report_mod.to_markdown(rep), report_mod.overview(rep)
    for words in ("Repeat windows diverged 4.5x", "was not found in 2 of 5 repeats",
                  "The repeats measured different processes",
                  "The window opened with the main thread already blocked",
                  "The same recording was given more than once"):
        line = next((x for x in whole.splitlines() if words in x), None)
        check(f"the whole report says it: {words}", line is not None, whole[:600])
        check(f"and the overview says it as it does: {words}",
              line in overview.splitlines(), overview[:900])


# --- reflect ---------------------------------------------------------------------

def _sitting(project: Path) -> None:
    (project / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    log = project / recorder.LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"ts": f"2026-08-19T10:0{i}:00+00:00", "cmd": cmd, "argv": [cmd, "a"],
             "cwd": "/p", "exit": 0, "ms": 1000, "version": "0.4.0"}
            for i, cmd in enumerate(("doctor", "analyze"))]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_one_session_outside_a_terminal_is_its_summary(tmp_path, monkeypatch):
    _sitting(tmp_path)
    monkeypatch.chdir(tmp_path)
    code, piped, err = _run("reflect", "--last", "--from-log", "--project", str(tmp_path))
    check("reflect exits 0", code == 0, err[-400:])
    page = next((tmp_path / ".echolot/reflect").glob("*.md"))
    whole = page.read_text(encoding="utf-8")
    check("a pipe gets the summary --all prints for a session",
          piped.startswith("# Reflect Summary") and "1 session(s)" in piped, piped[:400])
    check("not the whole report", whole.strip() not in piped, piped[:400])
    check("and is told where the rest is", "`reflect --full` prints it" in piped, piped[-300:])
    check("the report is written all the same", page.stem in err, err[-300:])

    code, full, err = _run("reflect", "--last", "--from-log", "--full",
                           "--project", str(tmp_path))
    check("--full prints the whole report",
          code == 0 and full == page.read_text(encoding="utf-8") + "\n", full[:300])
    code, shown, err = _run("reflect", "--last", "--from-log", "--project", str(tmp_path),
                            out=Terminal())
    check("and so does a terminal",
          code == 0 and shown == page.read_text(encoding="utf-8") + "\n", shown[:300])
