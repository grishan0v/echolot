#!/usr/bin/env python3
"""One reader for each file echolot writes and reads back (`echolot/files.py`, #833).

Pinned here, for each file:

  - what the current version writes reads back equal to what was written;
  - a file of a bad shape is refused with a sentence that names the part,
    never a traceback: wrong type, a missing field, a list where an object
    belongs, not UTF-8, cut short, an unknown schema;
  - an older schema reads through its upgrade, or is refused.

That each command and page that reads a file says that sentence is pinned
beside the command or the page (`tests/test_ui_after.py` for comparison.json).
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import files  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_compare_page import comparison, every_movement  # noqa: E402


def refused(got, said: str) -> bool:
    return isinstance(got, files.Refusal) and said in got.why


# --- the parts of a shape table ----------------------------------------------------------


def test_a_number_is_no_flag_and_a_flag_no_number():
    check("true is no number", files.NUMBER.fault(True, "a")[1] == "`a` is true or false, not a number")
    check("nor a whole one", files.WHOLE.fault(False, "a")[1] is not None)
    check("1.5 is no whole number", files.WHOLE.fault(1.5, "a")[1] == "`a` is a number, not a whole number")
    check("a flag is a flag", files.FLAG.fault(True, "a")[1] is None and files.FLAG.fault(1, "a")[1] is not None)
    check("a least value", files.Whole(1).fault(0, "n")[1] == "`n` is 0, below 1")
    check("one of a few words", files.OneOf(("open", "closed")).fault("ajar", "s")[1] == "`s` is `ajar`, not `open` or `closed`")


def test_an_object_names_the_first_part_that_does_not_hold():
    shape = files.Obj({"a": files.need(files.ListOf(files.Obj({"b": files.may(files.TEXT)})))})
    check("deep in a list", shape.fault({"a": [{"b": "x"}, {"b": 3}]}, "")[1] == "`a[1].b` is a number, not text")
    check("a needed field left out", shape.fault({}, "")[1] == "`a` is missing")
    check("a needed field that is null is missing", shape.fault({"a": None}, "")[1] == "`a` is missing")
    check("a field that may be there may be null", shape.fault({"a": [{"b": None}]}, "")[1] is None)
    check("keys the table does not name are left as they are",
          shape.fault({"a": [], "z": {1: 2}}, "") == ({"a": [], "z": {1: 2}}, None))


def test_a_field_that_drops_reads_as_absent_and_the_rest_reads():
    shape = files.Obj({"n": files.may(files.WHOLE, drop=True), "t": files.may(files.ListOf(files.TEXT, drop=True))})
    given = {"n": "x", "t": ["a", 1, "b"], "q": 1}
    got, wrong = shape.fault(given, "")
    check("no fault", wrong is None)
    check("the wrong one is gone, the odd items left out, the rest kept", got == {"t": ["a", "b"], "q": 1}, got)
    check("what was read is left as it was", given == {"n": "x", "t": ["a", 1, "b"], "q": 1})


def test_an_older_schema_reads_through_its_upgrades_in_turn():
    spec = files.Spec(name="x.json", schema=3, shape=files.Obj({"c": files.need(files.TEXT)}),
                      upgrades={1: lambda d: {**d, "b": str(d["a"])}, 2: lambda d: {**d, "c": d["b"] + "!"}})
    check("schema 1 becomes 3", spec.check({"schema": 1, "a": 7}) == {"schema": 3, "a": 7, "b": "7", "c": "7!"})
    check("one with no upgrade is refused", refused(spec.check({"schema": 0}), "an older echolot wrote it, in schema 0"))
    check("a newer one is refused", refused(spec.check({"schema": 4}), "a newer echolot wrote it, in schema 4"))


# --- comparison.json ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def written():
    """comparison.json as `compare --html` writes it."""
    return report_mod.to_json(comparison(*every_movement()))


def test_a_comparison_reads_back_as_it_was_written(written, tmp_path):
    (tmp_path / "comparison.json").write_text(written, encoding="utf-8")
    got = files.read_comparison(tmp_path / "comparison.json")
    check("it reads", not isinstance(got, files.Refusal), got)
    check("equal to what was written", got == json.loads(written))


def _first_row_with_both(cmp):
    return next(i for i, r in enumerate(cmp["rows"]) if r.get("before") and r.get("after"))


def _set(*place):
    *path, value = place

    def change(cmp):
        node = cmp
        for k in path[:-1]:
            node = node[k if k != "ROW" else _first_row_with_both(cmp)]
        node[path[-1]] = value
    return change


def _drop(key):
    return lambda cmp: cmp.pop(key)


BAD_COMPARISONS = [
    (_set("schema", 1), "an older echolot wrote it, in schema 1; this one reads schema 2"),
    (_set("schema", 3), "a newer echolot wrote it, in schema 3"),
    (_drop("schema"), "it carries no schema number"),
    (_set("schema", "2"), "its schema number is text, not a whole number"),
    (_set("kind", "report"), "its `kind` is `report`, not `comparison`"),
    (_drop("rows"), "`rows` is missing"),
    (_set("rows", {"a": 1}), "`rows` is an object, not a list"),
    (_set("rows", ["x"]), "`rows[0]` is text, not an object"),
    (_set("rows", "ROW", "before", "values", 7), "`rows[{i}].before.values` is a number, not a list"),
    (_set("rows", "ROW", "after", "values", True), "`rows[{i}].after.values` is true or false, not a list"),
    (_set("rows", "ROW", "before", "values", "zz"), "`rows[{i}].before.values` is text, not a list"),
    (_set("rows", "ROW", "before", "values", ["a"]), "`rows[{i}].before.values[0]` is text, not a number"),
    (_set("rows", "ROW", "before", []), "`rows[{i}].before` is a list, not an object"),
    (_set("after", "abc"), "`after` is text, not an object"),
    (_set("plain", "window", []), "`plain.window` is a list, not an object"),
    (_set("plain", "window", "after", "runs", "3"), "`plain.window.after.runs` is text, not a whole number"),
    (_set("plain", "checks", 0, "before", "zz"), "`plain.checks[0].before` is text, not an object"),
]


@pytest.mark.parametrize("change, said", BAD_COMPARISONS)
def test_a_comparison_of_a_bad_shape_says_which_part(written, tmp_path, change, said):
    cmp = json.loads(written)
    said = said.format(i=_first_row_with_both(cmp))
    change(cmp)
    (tmp_path / "comparison.json").write_text(json.dumps(cmp), encoding="utf-8")
    got = files.read_comparison(tmp_path / "comparison.json")
    check("refused, saying where", refused(got, said), got)
    check("as a file that is there", not got.absent)
    check("and the same of the value in hand", refused(files.check_comparison(copy.deepcopy(cmp)), said))


@pytest.mark.parametrize("data, said", [
    (b"\xff\xfe{", "it is not UTF-8 text"),
    (b"", "it is empty"),
    (b"[1, 2]", "it holds a list, not an object"),
    (b'"x"', "it holds text, not an object"),
])
def test_a_file_that_is_no_json_object_says_so(tmp_path, data, said):
    (tmp_path / "comparison.json").write_bytes(data)
    check(said, refused(files.read_comparison(tmp_path / "comparison.json"), said))


def test_a_comparison_cut_short_says_where(written, tmp_path):
    (tmp_path / "comparison.json").write_text(written[: len(written) // 2], encoding="utf-8")
    got = files.read_comparison(tmp_path / "comparison.json")
    check("it is not whole JSON, at a line", refused(got, "it is not whole JSON at line "), got)


def test_no_file_is_absent_rather_than_a_fault(tmp_path):
    got = files.read_comparison(tmp_path / "comparison.json")
    check("absent", isinstance(got, files.Refusal) and got.absent, got)


# --- hunt.json -----------------------------------------------------------------------------------


def test_a_record_reads_back_as_it_was_written(tmp_path):
    from echolot import hunt
    written = hunt.open_new(tmp_path, "why is the first frame late?", scenario="coldStart")
    got = files.read_hunt(hunt.path(tmp_path))
    check("it carries the schema the reader reads", written["schema"] == files.HUNT_SCHEMA)
    check("equal to what was written", got == json.loads(hunt.path(tmp_path).read_text(encoding="utf-8")) == written, got)


RECORD = {"n": 2, "question": "q", "status": "open", "collects": 1, "analyzes": 0, "reports": 3,
          "opened_at": "2026-11-24T10:00:00+00:00", "traces": ["a", "b"], "confirmed": {}}


@pytest.mark.parametrize("field, value", [
    ("n", 0), ("n", "x"), ("n", True), ("reports", "abc"), ("collects", -1), ("question", 5),
    ("opened_at", 1763719200), ("traces", 7), ("confirmed", []), ("verdict", "yes"),
])
def test_a_field_of_a_record_of_the_wrong_type_reads_as_absent(tmp_path, field, value):
    """The rest of the record still reads (#580)."""
    (tmp_path / "hunt.json").write_text(json.dumps({**RECORD, field: value}), encoding="utf-8")
    got = files.read_hunt(tmp_path / "hunt.json")
    check("it reads", not isinstance(got, files.Refusal), got)
    check("without that field, with the rest", field not in got
          and {k: v for k, v in RECORD.items() if k != field} == {k: v for k, v in got.items() if k != field}, got)


def test_a_trace_set_that_is_no_path_is_left_out(tmp_path):
    (tmp_path / "hunt.json").write_text(json.dumps({**RECORD, "traces": ["a", 3, None, "b"]}), encoding="utf-8")
    check("the paths are kept", files.read_hunt(tmp_path / "hunt.json")["traces"] == ["a", "b"])


@pytest.mark.parametrize("data, said", [
    (b"\xff\xfe{", "it is not UTF-8 text"),
    (b"  ", "it is empty"),
    (b'{"n": 1, "question": "q"', "it is not whole JSON at line 1"),
    (b"[]", "it holds a list, not an object"),
    (json.dumps({**RECORD, "schema": 2}).encode(), "a newer echolot wrote it, in schema 2; this one reads schema 1"),
    (json.dumps({**RECORD, "schema": "1"}).encode(), "its schema number is text, not a whole number"),
])
def test_a_record_that_does_not_read_says_why(tmp_path, data, said):
    (tmp_path / "hunt.json").write_bytes(data)
    got = files.read_hunt(tmp_path / "hunt.json")
    check(said, refused(got, said), got)


def test_a_record_with_no_schema_number_is_one_written_before_it_had_one(tmp_path):
    (tmp_path / "hunt.json").write_text(json.dumps(RECORD), encoding="utf-8")
    check("it reads, as it is", files.read_hunt(tmp_path / "hunt.json") == RECORD)


# --- runs.jsonl ----------------------------------------------------------------------------------


RUN = {"ts": "2026-11-24T10:00:00+00:00", "cmd": "doctor", "argv": ["doctor"], "cwd": "/p", "exit": 0,
       "ms": 812, "version": "0.18.0", "facts": {"checks": 219, "failed": [], "sandbox": "macos"}}


def test_the_log_reads_back_as_it_was_written(tmp_path):
    lines = [RUN, {**RUN, "cmd": "analyze", "facts": {"runs": 3}}]
    (tmp_path / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    check("every line, in order", files.read_runs(tmp_path / "runs.jsonl") == lines)


@pytest.mark.parametrize("field, value", [
    ("ts", 5), ("cmd", ["a"]), ("argv", "doctor"), ("exit", True), ("exit", "0"), ("ms", "fast"),
    ("version", 18), ("config", "x"), ("facts", [1]), ("error", {"a": 1}),
])
def test_a_field_of_a_run_of_the_wrong_type_is_dropped_and_the_line_kept(tmp_path, field, value):
    (tmp_path / "runs.jsonl").write_text(json.dumps({**RUN, field: value}) + "\n", encoding="utf-8")
    got = files.read_runs(tmp_path / "runs.jsonl")
    check("the run without that field", got == [{k: v for k, v in RUN.items() if k != field}], got)


@pytest.mark.parametrize("fact, value", [("failed", 2), ("checks", "219"), ("checks", False), ("sandbox", ["a"])])
def test_a_self_checks_fact_of_the_wrong_type_is_dropped(tmp_path, fact, value):
    """A `failed` that is a number was counted with `len` (#555)."""
    (tmp_path / "runs.jsonl").write_text(json.dumps({**RUN, "facts": {**RUN["facts"], fact: value}}) + "\n",
                                         encoding="utf-8")
    got = files.read_runs(tmp_path / "runs.jsonl")
    check("the fact is gone, the others kept",
          got[0]["facts"] == {k: v for k, v in RUN["facts"].items() if k != fact}, got)


def test_a_line_that_is_no_run_is_skipped_and_the_rest_read(tmp_path):
    good = json.dumps(RUN).encode()
    (tmp_path / "runs.jsonl").write_bytes(b"\n".join([good, b"\xff\xfe", b"[1, 2]", b"null", b"42", b"{cut",
                                                      b"", good[: len(good) // 2], good]))
    check("the two runs", files.read_runs(tmp_path / "runs.jsonl") == [RUN, RUN])


def test_no_log_is_no_runs(tmp_path):
    check("none", files.read_runs(tmp_path / "runs.jsonl") == [])
