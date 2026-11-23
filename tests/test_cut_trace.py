"""A recording that lost its tail is named as that (#568).

trace_processor drops an unfinished last packet without a word, so a file cut
in transit was analysed as if whole — or, once the cut reached the process
list, refused with advice to change a project.process that was right.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import fixture, recorder, selftest  # noqa: E402
from echolot.main import main  # noqa: E402
from echolot.tp import cut_short  # noqa: E402
from tests.support import check  # noqa: E402

WHOLE = fixture.build()


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _file(tmp_path: Path, name: str, body: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(body)
    return path


@pytest.mark.parametrize("removed", [1, 1000, len(WHOLE) // 2])
def test_a_cut_anywhere_but_a_packet_s_end_is_found(tmp_path: Path, removed: int) -> None:
    said = cut_short(_file(tmp_path, "cut.perfetto-trace", WHOLE[:-removed]))
    check(f"{removed} bytes off", said and "cut short" in said
          and f"ends at byte {len(WHOLE) - removed:,}" in said, said)


@pytest.mark.parametrize("name, body", [
    ("whole", WHOLE),
    ("gzip", gzip.compress(WHOLE)),
    ("empty", b""),
    ("text", b"this is a build log that was saved under the wrong name\n" * 20),
    ("json", b'{"schema": 1}'),
    # The first packet unfinished: no packet was whole, and trace_processor
    # refuses such a file by itself.
    ("first packet", WHOLE[:40]),
])
def test_what_is_whole_or_not_walked_says_nothing(tmp_path: Path, name: str,
                                                  body: bytes) -> None:
    check(name, cut_short(_file(tmp_path, "t.perfetto-trace", body)) is None)


def test_probe_warns_and_analyze_refuses(tmp_path: Path) -> None:
    cut = _file(tmp_path, "cut.perfetto-trace", WHOLE[:-1000])
    code, out, err = _run("probe", str(cut))
    check("probe reads it, and says it is cut",
          code == 0 and "[!] cut.perfetto-trace: the recording is cut short" in err
          and "## Processes" in out, (code, err))
    cfg = tmp_path / "echolot.yml"
    cfg.write_text(yaml.safe_dump(selftest.FIXTURE_CONFIG), encoding="utf-8")
    code, _, err = _run("analyze", str(cut), "-c", str(cfg), "-o", str(tmp_path / "out"))
    check("analyze refuses it by name, before any report",
          code == 2 and "cut.perfetto-trace: the recording is cut short" in err
          and not (tmp_path / "out" / "report.json").exists(), (code, err))
    whole = _file(tmp_path, "whole.perfetto-trace", WHOLE)
    code, _, err = _run("probe", str(whole))
    check("a whole file, no warning", code == 0 and "cut short" not in err, err)


def test_no_match_in_a_cut_file_blames_the_cut(tmp_path: Path) -> None:
    """The fifteen-character advice sent the reader to change a config that
    was right; on a cut file it is the file that is wrong."""
    cut = _file(tmp_path, "cut.perfetto-trace", WHOLE[:-1000])
    code, _, err = _run("names", str(cut), "--process", "com.example.longname.app*")
    check("names says the file is cut, and proposes no new mask",
          code == 2 and "The file is cut short" in err
          and "try --process" not in err, err)
