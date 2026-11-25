"""A slice name with brackets, written as the trace has it, matches (#1012).

A renderer that names its sections by subsystem writes `[render] DrawFrame`,
and `probe` said to write a name as shown. Every place echolot held such a
name against the trace read it as a GLOB alone, where `[render]` is one
letter of r, e, n, d: the end anchor matched nothing and the window ran to
the end of the trace, the `domains` entry was "never seen in this scenario"
beside the thousand times the trace held it, and the warning about the
anchor offered two other names. A name the user writes is read now as that
name and as a GLOB. doctor holds the same claims; these hold them through the
commands, over `fixture.bracketed`.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import anchors, fixture, names, recorder  # noqa: E402
from echolot.analyze import DETECTOR_DIR, _resolve_process, _setup_context  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import _name_coverage, main  # noqa: E402
from echolot.report import _merge_markers  # noqa: E402
from echolot.tp import TraceSession  # noqa: E402
from tests.support import check  # noqa: E402

APP = "com.example.app"
DRAW, END = fixture.BRACKET_DRAW, fixture.BRACKET_END
FRAME = "[[]render] Frame-*"      # what probe prints for `[render] Frame-10` and the rest


@pytest.fixture(scope="module")
def trace(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("bracketed") / "render_iter000.perfetto-trace"
    path.write_bytes(fixture.bracketed())
    return path


def _config(root: Path, end: str | None = None, domains: tuple[str, ...] = ()) -> Path:
    scenario = {"name": "render", "start": {"name": "AppStart"}}
    if end is not None:
        scenario["end"] = {"name": end}
    raw = {"project": {"package": APP, "process": APP}, "scenario": scenario,
           "domains": [{"slice": s, "module": "render"} for s in domains]}
    path = root / "echolot.yml"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _analyze(root: Path, config: Path, *traces: Path) -> tuple[dict, str]:
    code, _, err = _run("analyze", "-c", str(config), "-o", str(root / "out"), *map(str, traces))
    check("analyze exits 0", code == 0, err[-800:])
    return (json.loads((root / "out" / "report.json").read_text(encoding="utf-8")),
            (root / "out" / "report.md").read_text(encoding="utf-8"))


# --- the rule ---------------------------------------------------------------------

@pytest.mark.parametrize("written, name, matched", [
    (DRAW, DRAW, True),                       # as the trace has it
    ("[[]render] DrawFrame", DRAW, True),     # escaped, as `domains` writes it
    ("[render]*", DRAW, False),               # a GLOB still: `[render]` is one letter there
    ("*DrawFrame", DRAW, True),
    ("Frame-*", "Frame-10", True),
    (DRAW, "x DrawFrame", False),
])
def test_a_written_name_matches_itself_and_its_glob(written: str, name: str, matched: bool) -> None:
    db = sqlite3.connect(":memory:")
    sql = db.execute(f"SELECT {names.matches_sql('?', written).replace('?', ':n')}",
                     {"n": name}).fetchone()[0]
    check(f"{written!r} against {name!r}", names.matches(name, written) is matched
          and bool(sql) is matched, (names.matches(name, written), sql))


def test_a_name_is_written_as_it_is_and_with_a_number_escaped() -> None:
    check("no number: as it is", names.as_written(DRAW) == DRAW)
    check("a number: a GLOB, its own bracket escaped",
          names.as_written("[render] Frame-10") == FRAME, names.as_written("[render] Frame-10"))
    check("which matches the name", names.matches("[render] Frame-10", FRAME))
    check("escaped matches the name and nothing else",
          names.matches(DRAW, names.escaped(DRAW)) and not names.matches("r DrawFrame", names.escaped(DRAW)))


# --- the window and the markers ---------------------------------------------------

def test_an_anchor_and_a_domains_entry_written_as_shown_match(trace: Path, tmp_path: Path) -> None:
    rep, md = _analyze(tmp_path, _config(tmp_path, END, (DRAW,)), trace)
    w = rep["window"]
    check("the end anchor matched", w["end_anchor"]["matches"] == 1, w["end_anchor"])
    check("and closed the window there", w["duration_ms"] == fixture.BRACKET_END_MS, w)
    check("no warning about it", "anchor `[render] Present` was not found" not in md, md[:1500])
    code, out, err = _run("report", "--markers", str(tmp_path / "out" / "report.json"))
    check("report --markers exits 0", code == 0, err[-400:])
    check("the entry is a row", f"| {DRAW} | 3 |" in out, out)
    check("and not absent", "Not in the window" not in out, out)


def test_over_repeats_a_name_is_absent_only_when_no_repeat_held_it() -> None:
    row = {"location": DRAW, "count": 3, "self_ms": 1.0, "total_ms": 1.0, "max_ms": 1.0,
           "detail": "RenderLoop"}

    def side(rows: list, absent: list) -> dict:
        return {"markers": {"prefix": "AGENTTMP_", "globs": ["AGENTTMP_*", DRAW],
                            "rows": rows, "absent": absent}}

    check("one repeat held it", _merge_markers([side([], [DRAW]), side([row], [])], 2)["absent"] == [])
    check("none did", _merge_markers([side([], [DRAW]), side([], [DRAW])], 2)["absent"] == [DRAW])


# --- probe and the warning --------------------------------------------------------

def test_what_probe_prints_closes_the_window_when_pasted(trace: Path, tmp_path: Path) -> None:
    code, out, err = _run("probe", str(trace), "--process", APP)
    check("probe exits 0", code == 0, err[-400:])
    shown = [line.split("|")[1].strip() for line in out.split("## Start candidates")[0].splitlines()
             if line.startswith("| ") and "---" not in line][1:]
    check("the names as written", {DRAW, FRAME, END} <= set(shown), shown)
    check("with what the escape means", "`[`, `*` or `?` of the name's own is written `[[]`" in out, out)
    # Where the first of each ends: the window opens with `AppStart` at 0.
    for name, end_ms in ((DRAW, 80.0), (FRAME, 65.0), (END, float(fixture.BRACKET_END_MS))):
        root = tmp_path / re.sub(r"\W", "_", name)
        root.mkdir()
        rep, _ = _analyze(root, _config(root, name), trace)
        check(f"{name} pasted as the end anchor", rep["window"]["duration_ms"] == end_ms,
              rep["window"])


def test_an_anchor_that_missed_is_offered_a_form_that_matches(trace: Path, tmp_path: Path) -> None:
    rep, md = _analyze(tmp_path, _config(tmp_path, "[render] Frame"), trace)
    near = rep["window"]["end_anchor"].get("near") or []
    check("the closest, escaped", near[:1] == [FRAME], rep["window"]["end_anchor"])
    check("and said so", f"The trace holds `{FRAME}`" in md, md[:2000])


# --- the fold, the masks, the code ------------------------------------------------

def test_an_own_name_keeps_its_number_and_a_mask_written_as_a_name_matches(trace: Path) -> None:
    with TraceSession(trace) as tp:
        upid = _resolve_process(tp, APP, str(trace))[0]["upid"]
        cfg = {"project": {"package": APP, "process": APP},
               "scenario": {"name": "render", "start": {"name": "AppStart"}},
               "domains": [{"slice": "[render] Frame-10", "module": "render"}]}
        _setup_context(tp, Config(cfg), upid)
        folded = {r["name"]: r["family"] for r in tp.query("SELECT name, family FROM _name_family")}
        covered, _ = _name_coverage(tp, upid, [("mask", "name", DRAW)])
    check("the config's own name as written", "[render] Frame-10" not in folded, folded)
    check("the others folded", folded.get("[render] Frame-11") == "[render] Frame-#", folded)
    check("a mask written as the name sees it", covered.get(DRAW) == {"mask"}, dict(covered))


def test_every_param_glob_in_the_queries_goes_with_the_name_itself() -> None:
    alone = []
    for path in sorted([*DETECTOR_DIR.glob("*.sql"), DETECTOR_DIR.parent / "context.sql"]):
        text = " ".join(path.read_text(encoding="utf-8").split())
        for m in re.finditer(r"([\w.]+) GLOB '\{\{(\w+)\}\}'", text):
            col, param = m.groups()
            if f"{col} = '{{{{{param}}}}}' OR {col} GLOB" not in text:
                alone.append(f"{path.name}: {m.group(0)}")
    check("no mask held against a GLOB alone", not alone, alone)


def test_a_code_string_with_brackets_is_the_anchor_it_spells(tmp_path: Path) -> None:
    src = tmp_path / "app/src/main/java/Feed.kt"
    src.parent.mkdir(parents=True)
    src.write_text('object Feed { const val SHOWN = "Feed[main] shown" }\n', encoding="utf-8")
    check("held, not said to be written nowhere",
          anchors.unwritten(tmp_path, ["Feed[main] shown"], "AGENTTMP_") == {})
