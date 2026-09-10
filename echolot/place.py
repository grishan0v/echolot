"""From a row of the report to a line in the checkout.

ART's contention slice names both sides of a lock — the thread holding it
and where it is, the method waiting for it and where that is — with the
file and line the runtime had for each. The report carried that string as
evidence and stopped there, so the reader's next move was a grep: on a real
hunt the subagent spent forty-six percent of its window reading the
application to find `PizzeriaService.updatePizzeriasForCountry`, whose file
name was in the row all along. `main_thread_block` names a class the same
way — `com.dodopizza.android.rive.RiveAnimationNonInteractiveView` is a file
in the checkout, and the row did not say which.

This module reads those names off a row and puts the file next to them:
`places` in the json with the symbol, the path and the line, and one short
`code` column in the markdown. Nothing here moves a measurement.

The walk is `domains.source_files`, in the direction that keeps the import
graph a tree: `domains` imports nothing of ours.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .domains import source_files

# The informative shape of ART's contention slice:
#   monitor contention with owner <thread> (<tid>) at <frame> waiters=<n>
#   blocking from <frame>
# The other shape, `Lock contention on a monitor lock (owner tid: N)`, names
# nobody and is left alone.
_CONTENTION = re.compile(
    r"^monitor contention with owner (?P<owner>.+?) \((?P<tid>\d+)\) at "
    r"(?P<at>.+?) waiters=(?P<waiters>\d+) blocking from (?P<blocked>.+)$")

# A frame as the runtime prints it: `<ret> pkg.Class.method(args)(File.kt:41)`.
# The return type is a single token and is not always there. What is in the
# last parentheses is the file and line when the runtime had them, `:-1` or
# `:-2` when it did not — a release build, a native method — and a
# dependency's coordinate for code that came out of a dynamite module.
_FRAME = re.compile(
    r"^(?:\S+\s+)?(?P<symbol>[\w$.<>]+)\((?P<args>[^()]*)\)\((?P<where>[^()]*)\)$")
_WHERE = re.compile(r"^(?P<file>[\w$]+\.(?:kt|java)):(?P<line>-?\d+)$")

# A location that is a class rather than a slice: two or more lowercase
# package segments and a capitalised name. `bindApplication` and
# `Choreographer#doFrame` are not that, and neither is `inflate`.
_CLASS = re.compile(r"^(?:[a-z_]\w*\.){2,}(?P<cls>[A-Z]\w*)$")


@dataclass
class Place:
    role: str            # owner, blocked, or location
    symbol: str          # pkg.Class.method, or pkg.Class for a location
    file: str | None     # relative to the root; None when not in this checkout
    line: int | None
    exact: bool          # one candidate, or the package agreed


def index(root: Path) -> dict[str, list[Path]]:
    """Every Kotlin and Java source under the root, by file name."""
    found: dict[str, list[Path]] = {}
    for path in source_files(root):
        found.setdefault(path.name, []).append(path)
    return found


def parse_contention(detail: str) -> dict[str, Any] | None:
    """The two frames out of a contention slice, or None for any other name."""
    m = _CONTENTION.match(detail or "")
    if not m:
        return None
    return {"owner_thread": m.group("owner"), "owner_tid": int(m.group("tid")),
            "at": m.group("at"), "blocked": m.group("blocked"),
            "waiters": int(m.group("waiters"))}


def parse_frame(frame: str) -> tuple[str, str | None, int | None] | None:
    """(symbol, file name, line) — file and line None when the runtime had none."""
    m = _FRAME.match(frame.strip())
    if not m:
        return None
    where = _WHERE.match(m.group("where").strip())
    if not where:
        return m.group("symbol"), None, None
    line = int(where.group("line"))
    return m.group("symbol"), where.group("file"), (line if line > 0 else None)


def _choose(candidates: list[Path], symbol: str) -> tuple[Path, bool]:
    """The file for a symbol among the files of that name.

    Two modules holding a `Mapper.kt` is ordinary, and the package from the
    symbol settles it. One file of that name in the whole checkout is taken
    whatever the package says — Kotlin lets a class live in a directory
    that does not spell out its package — and `exact` says which of the two
    happened.
    """
    owner = symbol.split("$", 1)[0]
    parts = owner.split(".")
    # The class is the first capitalised segment; everything before it is
    # the package. `pkg.Class.method` and `pkg.Class` both come here.
    package = []
    for part in parts:
        if part[:1].isupper():
            break
        package.append(part)
    wanted = "/".join(package)
    if wanted:
        for path in candidates:
            if f"/{wanted}/" in path.as_posix():
                return path, True
    return candidates[0], len(candidates) == 1


_KOTLIN_FUN = r"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?{name}\s*\("
_JAVA_METHOD = r"\b[\w<>\[\],.]+\s+{name}\s*\([^;{{}}]*\)\s*(?:throws[^{{;]*)?\{{"


def declared_at(path: Path, method: str) -> int | None:
    """The line a method is declared on, for a frame the runtime gave no line.

    A release build strips line numbers, and `(File.kt:-1)` is what every
    frame then says. The declaration is the next best address: it is where
    a reader opens the file anyway. Kotlin's synthetic names are cut back to
    what was written — `fileStorage_delegate$lambda$0` is the initialiser
    of `val fileStorage by lazy`.
    """
    name = method.split("$", 1)[0]
    if not name or name.startswith("<"):
        return None
    patterns = [_KOTLIN_FUN.format(name=re.escape(name)),
                _JAVA_METHOD.format(name=re.escape(name))]
    if name.endswith("_delegate"):
        patterns.append(rf"\bva[lr]\s+{re.escape(name[:-len('_delegate')])}\b")
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for pattern in patterns:
        rx = re.compile(pattern)
        for i, line in enumerate(lines, 1):
            if rx.search(line):
                return i
    return None


def locate(symbol: str, file: str | None, line: int | None,
           idx: dict[str, list[Path]], root: Path, role: str) -> Place:
    """Where a symbol is in this checkout, or a Place with no file."""
    owner = symbol.split("$", 1)[0]
    parts = owner.split(".")
    cls = next((p for p in parts if p[:1].isupper()), None)
    names = [file] if file else []
    if cls:
        names += [f"{cls}.kt", f"{cls}.java"]
    candidates = next((idx[n] for n in names if idx.get(n)), None)
    if not candidates:
        return Place(role, symbol, None, None, False)
    path, exact = _choose(candidates, symbol)
    if line is None and cls and parts and parts[-1] != cls and not parts[-1][:1].isupper():
        line = declared_at(path, parts[-1])
    return Place(role, symbol, path.relative_to(root).as_posix(), line, exact)


def places_of(row: dict[str, Any], idx: dict[str, list[Path]], root: Path) -> list[Place]:
    """Everything a row names that could be a file, placed where it can be.

    Both frames of a contention slice are kept whether or not they are in
    the checkout: an owner parked in `Unsafe.park` is a fact about the lock —
    the holder is waiting on something else while holding it — and dropping
    it because the JDK is not in the repository would lose that. A location
    that names a class is kept only when the class is here; `android.view.View`
    placed nowhere says nothing.
    """
    out: list[Place] = []
    found = parse_contention(str(row.get("detail") or ""))
    if found:
        for role in ("owner", "blocked"):
            frame = parse_frame(found["at"] if role == "owner" else found["blocked"])
            if frame:
                out.append(locate(*frame, idx, root, role))
    m = _CLASS.match(str(row.get("location") or ""))
    if m:
        here = locate(m.group(0), None, None, idx, root, "location")
        if here.file:
            out.append(here)
    return out


def code_column(places: list[Place]) -> str | None:
    """The markdown cell: file names and lines, roles where there are two."""
    parts = []
    for p in places:
        if not p.file:
            continue
        name = p.file.rsplit("/", 1)[-1]
        where = f"{name}:{p.line}" if p.line else name
        parts.append(where if p.role == "location" else f"{p.role} at {where}")
    return " · ".join(parts) or None


def annotate(report: dict[str, Any], root: Path) -> int:
    """Adds `places` and `code` to every row that names something placeable.

    Returns how many rows were placed. The index is built on first use, so a
    report with nothing to place costs no walk at all. The whole checkout is
    walked rather than `project.source_root`: that key's example value is
    `app/src/main/kotlin`, and a lock in `domain/` is exactly the kind of
    place a project with several modules has.
    """
    idx: dict[str, list[Path]] | None = None
    placed = 0
    for det in report.get("detectors") or []:
        for row in det.get("rows") or []:
            needs = _CONTENTION.match(str(row.get("detail") or "")) \
                or _CLASS.match(str(row.get("location") or ""))
            if not needs:
                continue
            if idx is None:
                idx = index(root) if root.is_dir() else {}
            found = places_of(row, idx, root)
            if not found:
                continue
            row["places"] = [asdict(p) for p in found]
            cell = code_column(found)
            if cell:
                row["code"] = cell
                placed += 1
    return placed
