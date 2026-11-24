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


# --- report.json ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report_written():
    """report.json as `analyze` writes it."""
    from tests.test_report_page import known
    return report_mod.to_json(known())


def test_a_report_reads_back_as_it_was_written(report_written, tmp_path):
    (tmp_path / "report.json").write_text(report_written, encoding="utf-8")
    got = files.read_report(tmp_path / "report.json")
    check("it carries the schema the reader reads", json.loads(report_written)["schema"] == files.REPORT_SCHEMA)
    check("equal to what was written", got == json.loads(report_written), got)


BAD_REPORTS = [
    (_set("schema", 2), "a newer echolot wrote it, in schema 2; this one reads schema 1"),
    (_set("schema", 0), "an older echolot wrote it, in schema 0"),
    (_set("schema", True), "its schema number is true or false, not a whole number"),
    (_set("kind", "comparison"), "it is a comparison, not a report.json"),
    (_set("detectors", "x"), "`detectors` is text, not a list"),
    (_set("detectors", 0, "gc_pressure"), "`detectors[0]` is text, not an object"),
    (_set("detectors", 0, "id", 7), "`detectors[0].id` is a number, not text"),
    (_set("detectors", 0, "id", None), "`detectors[0].id` is missing"),
    (_set("detectors", 2, "rows", {"a": 1}), "`detectors[2].rows` is an object, not a list"),
    (_set("detectors", 2, "rows", ["Sync Worker"]), "`detectors[2].rows[0]` is text, not an object"),
    (_set("markers", []), "`markers` is a list, not an object"),
    (_set("markers", {"rows": "x"}), "`markers.rows` is text, not a list"),
    (_set("markers", {"absent": 3}), "`markers.absent` is a number, not a list"),
    (_set("summary", []), "`summary` is a list, not an object"),
    (_set("summary", "detectors_run", "6"), "`summary.detectors_run` is text, not a whole number"),
    (_set("summary", "detectors_fired", True), "`summary.detectors_fired` is true or false, not a whole number"),
    (_set("config", "x"), "`config` is text, not an object"),
    (_set("config", {"sha": 5}), "`config.sha` is a number, not text"),
    (_set("config", {"defaults": "no"}), "`config.defaults` is text, not true or false"),
    (_set("traces", "ab"), "`traces` is text, not a list"),
    (_set("generated_at", 1763719200), "`generated_at` is a number, not text"),
]


@pytest.mark.parametrize("change, said", BAD_REPORTS)
def test_a_report_of_a_bad_shape_says_which_part(report_written, tmp_path, change, said):
    data = json.loads(report_written)
    change(data)
    (tmp_path / "report.json").write_text(json.dumps(data), encoding="utf-8")
    got = files.read_report(tmp_path / "report.json")
    check("refused, saying where", refused(got, said), got)
    check("its schema alone makes it stale", got.stale == ("echolot wrote it" in said), got)


@pytest.mark.parametrize("data, said", [
    (b"\xff\xfe{", "it is not UTF-8 text"),
    (b"\n", "it is empty"),
    (b'{"schema": 1, "detectors": [', "it is not whole JSON at line 1"),
    (b"42", "it holds a number, not an object"),
])
def test_a_report_that_is_no_json_object_says_so(tmp_path, data, said):
    (tmp_path / "report.json").write_bytes(data)
    check(said, refused(files.read_report(tmp_path / "report.json"), said))


def test_a_report_with_no_schema_number_is_one_of_schema_1(report_written, tmp_path):
    data = json.loads(report_written)
    del data["schema"]
    (tmp_path / "report.json").write_text(json.dumps(data), encoding="utf-8")
    check("it reads, as it is", files.read_report(tmp_path / "report.json") == data)


def test_raising_the_report_schema_is_one_line_and_an_upgrade(report_written):
    """What #832 does: a new number, and either an upgrade from the old one or
    a refusal that names `echolot analyze`."""
    import dataclasses
    old = json.loads(report_written)
    refusing = dataclasses.replace(files.REPORT, schema=2)
    check("without an upgrade, a schema-1 report is refused for its schema",
          refused(refusing.check(old), "an older echolot wrote it, in schema 1; this one reads schema 2")
          and refusing.check(old).stale)
    upgrading = dataclasses.replace(files.REPORT, schema=2, upgrades={1: lambda d: {**d, "upgraded": True}})
    got = upgrading.check(old)
    check("with one, it reads as schema 2", got["schema"] == 2 and got["upgraded"] is True, got)


# --- collect.json and a job's state ------------------------------------------------------------


def test_the_progress_a_collect_writes_reads_back(tmp_path):
    from echolot import runner
    progress = runner.Progress(tmp_path / "collect.json")
    progress.update(started=1763719200.5, pid=4242, scenario="coldStart", iterations="five", done=0)
    got = files.read_collect(tmp_path / "collect.json")
    check("equal to what was written, with the schema the reader reads",
          got == progress.state and got["schema"] == files.COLLECT_SCHEMA, got)


COLLECT = {"started": 1763719200.5, "pid": 4242, "scenario": "coldStart", "done": 2, "iterations": 5}


@pytest.mark.parametrize("field, value, said", [
    ("started", None, "`started` is missing"),
    ("started", "now", "`started` is text, not a number"),
    ("started", True, "`started` is true or false, not a number"),
    ("started", 10**400, "`started` is too long, not a finite number"),
    ("finished", "later", "`finished` is text, not a number"),
    ("pid", 0, "`pid` is 0, below 1"),
    ("pid", -1, "`pid` is -1, below 1"),
    ("pid", 2**31, f"`pid` is {2**31}, above {2**31 - 1}"),
    ("done", "2", "`done` is text, not a whole number"),
    ("exit", True, "`exit` is true or false, not a whole number"),
    ("scenario", ["a"], "`scenario` is a list, not text"),
    ("schema", 2, "a newer echolot wrote it, in schema 2"),
])
def test_a_collect_of_a_bad_shape_says_which_part(tmp_path, field, value, said):
    data = {**COLLECT, field: value}
    (tmp_path / "collect.json").write_text(json.dumps(data), encoding="utf-8")
    check(said, refused(files.read_collect(tmp_path / "collect.json"), said))


def test_infinity_in_a_collect_is_no_time(tmp_path):
    (tmp_path / "collect.json").write_text('{"started": Infinity}', encoding="utf-8")
    check("refused", refused(files.read_collect(tmp_path / "collect.json"), "`started` is inf, not a finite number"))


def test_iterations_is_held_to_no_type(tmp_path):
    (tmp_path / "collect.json").write_text(json.dumps({**COLLECT, "iterations": "five"}), encoding="utf-8")
    check("it reads", files.read_collect(tmp_path / "collect.json") == {**COLLECT, "iterations": "five"})


JOB = {"job": "check", "pid": 4242, "started": 1763719200.5, "finished": None, "what": {},
       "steps": [{"id": "collect", "status": "running", "started": 1763719201.0, "exit": None}]}


@pytest.mark.parametrize("change, said", [
    (_drop("steps"), "`steps` is missing"),
    (_set("steps", {"a": 1}), "`steps` is an object, not a list"),
    (_set("steps", 0, "id", 3), "`steps[0].id` is a number, not text"),
    (_set("steps", 0, "started", "soon"), "`steps[0].started` is text, not a number"),
    (_set("steps", 0, "log", 1), "`steps[0].log` is a number, not text"),
    (_set("pid", 0), "`pid` is 0, below 1"),
    (_set("what", []), "`what` is a list, not an object"),
    (_set("why", 1), "`why` is a number, not text"),
])
def test_a_job_of_a_bad_shape_says_which_part(tmp_path, change, said):
    data = copy.deepcopy(JOB)
    change(data)
    (tmp_path / "job.json").write_text(json.dumps(data), encoding="utf-8")
    check(said, refused(files.read_job(tmp_path / "job.json"), said))


def test_a_job_reads_back(tmp_path):
    (tmp_path / "job.json").write_text(json.dumps(JOB), encoding="utf-8")
    check("as written", files.read_job(tmp_path / "job.json") == JOB)
