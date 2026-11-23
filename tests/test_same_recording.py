"""One recording is one repeat, however many times it is named (#565).

A path given twice, a byte copy under another name, a symlink beside its
file: each used to count as one more repeat. Every value then occurred twice
on its side, `compare`'s `few` warning went away, and rows held on four
recordings that, named once, held none.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import compare as compare_mod  # noqa: E402
from echolot import fixture, recorder  # noqa: E402
from echolot.main import _distinct_recordings, main  # noqa: E402
from tests.support import check  # noqa: E402

CONFIG = """\
project:
  package: com.example.app
  process: com.example.app
scenario:
  name: fixture
  start: {name: AppStart}
  end: {name: Screen.firstFrame}
"""


def _analyze(root: Path, out: str, *traces: str) -> tuple[dict, str, str]:
    (root / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    err = io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(err):
        code = main(["analyze", "-c", str(root / "echolot.yml"), "-o", out, *traces])
    assert code == 0, err.getvalue()[-800:]
    where = root / out
    return (json.loads((where / "report.json").read_text()),
            (where / "report.md").read_text(), err.getvalue())


def _trace(root: Path, name: str, **build) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fixture.build(**build))
    return str(path)


def test_a_path_named_twice_is_one_repeat(tmp_path: Path) -> None:
    trace = _trace(tmp_path, "a.perfetto-trace")
    rep, md, err = _analyze(tmp_path, "out", trace, trace)
    check("one run, as one trace", rep.get("runs", 1) == 1 and not rep.get("traces"), rep.get("runs"))
    check("and the report says the path was repeated",
          rep["same_recording"] == [[trace, trace]] and "given more than once" in md
          and "given more than once" in err, md[:600])


def test_a_byte_copy_and_a_symlink_are_the_same_recording(tmp_path: Path) -> None:
    trace = _trace(tmp_path, "a.perfetto-trace")
    copy = tmp_path / "copy" / "other_name.perfetto-trace"
    copy.parent.mkdir()
    shutil.copyfile(trace, copy)
    link = tmp_path / "link.perfetto-trace"
    link.symlink_to(trace)
    kept, same = _distinct_recordings([trace, str(copy), str(link)])
    check("the first is kept, the others named with it",
          kept == [trace] and same == [[trace, str(copy), str(link)]], (kept, same))


def test_different_recordings_of_one_size_are_kept(tmp_path: Path) -> None:
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"x" * 64)
    b.write_bytes(b"y" * 64)
    kept, same = _distinct_recordings([str(a), str(b), str(tmp_path / "missing")])
    check("same size, other bytes: two recordings; a missing path is left to analyze",
          kept == [str(a), str(b), str(tmp_path / "missing")] and same == [], (kept, same))


def test_two_repeats_a_side_named_twice_still_get_no_verdict(tmp_path: Path) -> None:
    before = [_trace(tmp_path, "b/1.perfetto-trace"),
              _trace(tmp_path, "b/2.perfetto-trace", frames=False)]
    after = [_trace(tmp_path, "a/1.perfetto-trace", composables=False),
             _trace(tmp_path, "a/2.perfetto-trace", crowded=True)]
    rb, _, _ = _analyze(tmp_path, "before", *before, *before)
    ra, _, _ = _analyze(tmp_path, "after", *after, *after)
    check("two runs a side, not four", rb["runs"] == 2 and ra["runs"] == 2,
          (rb["runs"], ra["runs"]))
    cmp = compare_mod.build(rb, ra, before_path="b", after_path="a")
    check("the few warning stays, and no row holds",
          "few" in {w["id"] for w in cmp["warnings"]}
          and all(r["holds"] is None for r in cmp["rows"]), cmp["warnings"])
