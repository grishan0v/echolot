"""A report that cannot be written leaves the output directory as it was (#579).

They were written straight to their names, so a full disk left a `report.json`
of 0 bytes — which `report` then refused and `status` read as a report that
does not load — or a whole new `report.json` beside an empty `report.md`,
under an exit 2 that said there was no new report. A file-size limit stands
in for the full disk: past it, a write fails with EFBIG the way it fails with
ENOSPC, and Python ignores the signal that comes with it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from echolot import atomic, fixture, selftest  # noqa: E402
from tests.support import check  # noqa: E402

pytest.importorskip("resource")


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("writes")
    (root / "fx.perfetto-trace").write_bytes(fixture.build())
    (root / "echolot.yml").write_text(yaml.safe_dump(selftest.FIXTURE_CONFIG),
                                      encoding="utf-8")
    return root


# The limit set inside the process that writes, for the writes alone. Set at
# the start, trace_processor inherits it and dies of SIGXFSZ before there is
# anything to write; left in place to the end, it also fails what runs at
# exit — on CI, coverage writing its data file for this child, whose
# traceback then ends stderr. So only the soft limit is lowered, when
# `to_markdown` is first called (for the writes), and raised again once
# echolot is done.
CAPPED = """
import resource, runpy, sys
from echolot import report
limit = int(sys.argv.pop(1))
soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
drawn = report.to_markdown
def capped(*a, **k):
    if limit:
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, hard))
    return drawn(*a, **k)
report.to_markdown = capped
sys.argv[0] = "echolot"
try:
    runpy.run_module("echolot.main", run_name="__main__")
finally:
    resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
"""


def _analyze(root: Path, out: Path, limit: int = 0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", CAPPED, str(limit), "analyze", "fx.perfetto-trace",
         "-o", str(out)],
        cwd=root, capture_output=True, text=True, timeout=300,
        env=dict(os.environ, PYTHONPATH=str(ROOT), ECHOLOT_NO_RECORD="1",
                 PYTHONDONTWRITEBYTECODE="1"))


def test_a_write_that_fails_leaves_nothing_new(project: Path) -> None:
    whole = project / "whole"
    done = _analyze(project, whole)
    assert done.returncode == 0, done.stderr[-800:]
    sizes = {p.name: p.stat().st_size for p in whole.iterdir()}
    limit = min(sizes["report.json"], sizes["report.md"]) // 2

    fresh = project / "fresh"
    done = _analyze(project, fresh, limit)
    check("exit 2, a sentence", done.returncode == 2 and "error:" in done.stderr
          and "Traceback" not in done.stderr, done.stderr[-800:])
    left = sorted(p.name for p in fresh.iterdir()) if fresh.exists() else []
    check("and nothing in the directory: no empty report, no temporary file",
          left == [], left)

    kept = project / "kept"
    shutil.copytree(whole, kept)
    before = {p.name: p.read_bytes() for p in kept.iterdir()}
    done = _analyze(project, kept, limit)
    check("exit 2 over an existing report too", done.returncode == 2, done.stderr[-800:])
    after = {p.name: p.read_bytes() for p in kept.iterdir()}
    check("which stays byte for byte as it was", after == before,
          sorted(set(after) ^ set(before)))


def test_the_pair_is_never_one_new_and_one_old(tmp_path: Path, monkeypatch) -> None:
    """The second file failing must not leave the first one put in place."""
    md, js = tmp_path / "report.md", tmp_path / "report.json"
    md.write_text("old md", encoding="utf-8")
    js.write_text("old json", encoding="utf-8")
    real = atomic._temporary

    def second_fails(path, text):
        if path == js:
            raise OSError(28, "No space left on device")
        return real(path, text)

    monkeypatch.setattr(atomic, "_temporary", second_fails)
    with pytest.raises(OSError):
        atomic.write_all([(md, "new md"), (js, "new json")])
    check("both old", (md.read_text(encoding="utf-8"), js.read_text(encoding="utf-8"))
          == ("old md", "old json"))
    check("no temporary left", sorted(p.name for p in tmp_path.iterdir())
          == ["report.json", "report.md"])


def test_a_written_file_has_an_ordinary_mode(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    atomic.write_text(path, "{}")
    umask = os.umask(0)
    os.umask(umask)
    check("0o666 less the umask, as write_text gives",
          path.stat().st_mode & 0o777 == 0o666 & ~umask, oct(path.stat().st_mode))
