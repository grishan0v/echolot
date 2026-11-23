"""Files written whole or not at all (#579).

A report written straight to its name is truncated first and filled after: a
full disk or a kill in between left a `report.json` of 0 bytes, which
`report` then refused and `status` read as a report that does not load — or,
with a little more room, a whole new `report.json` beside an empty
`report.md`, under an exit that said the run had failed. Each file is written
to a temporary name beside it and renamed onto its own: a rename within one
directory replaces the old file in one step, so a reader sees the old file or
the new one, and never a part of either.
"""

from __future__ import annotations

import contextlib
import os
import secrets
from pathlib import Path


def write_text(path: Path, text: str) -> None:
    write_all([(path, text)])


def write_all(files: list[tuple[Path, str]]) -> None:
    """Every file written in full before any is put in place.

    So a failure on any of them leaves all as they were: a pair such as
    `report.json` and `report.md` is never one new and one old. They are put
    in place in the order given; put the one that is read as the result —
    `report.json` — last. Raises OSError, with no temporary file left behind.
    """
    ready: list[tuple[Path, Path]] = []
    try:
        for path, text in files:
            ready.append((_temporary(path, text), path))
        for tmp, path in ready:
            os.replace(tmp, path)
    finally:
        for tmp, _ in ready:
            with contextlib.suppress(OSError):
                tmp.unlink()


def _temporary(path: Path, text: str) -> Path:
    """`text` in a new file beside `path`, under a name no other writer has.

    Created with the mode an ordinary write would give it — 0o666 less the
    umask — rather than the 0o600 of a `tempfile`, since it becomes the file.
    A dot first, so a listing of the directory does not show it.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}-{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return tmp
