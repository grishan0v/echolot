"""`compare -o` with no config: the flag is where the files go."""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import recorder  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402


def _run(*argv: str) -> tuple[int, str]:
    err = io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, err.getvalue()


def test_o_without_a_config_is_where_the_files_go(tmp_path: Path, demo_report,
                                                  monkeypatch) -> None:
    (tmp_path / "b.json").write_text(json.dumps(demo_report), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps(demo_report), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, err = _run("compare", "b.json", "a.json", "-o", "out")
    check("both files in out", code == 0 and (tmp_path / "out" / "comparison.md").is_file()
          and (tmp_path / "out" / "comparison.json").is_file(), err)
    code, err = _run("compare", "b.json", "a.json")
    check("and with neither, the note", code == 0 and "nothing was written" in err
          and not (tmp_path / ".echolot").exists(), err)
