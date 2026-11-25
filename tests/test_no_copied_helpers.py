"""No helper is written out twice in the package (#994).

Small helpers were copied from module to module: how a report written by an
older version is read, how many runs a report is of, whether a value is a
number, `1 run` against `2 runs`. A fix to one copy left the others as they
were. Each now has one home (`files.py` for reading a report, `words.py` for
numbers and counts, the module that owns the idea for the rest) and the
others import it.

The scan: every top-level function of the package, its signature and body
dumped as a tree, the docstring left out. Two modules with the same dump
wrote the same function twice.
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

PACKAGE = Path(__file__).resolve().parent.parent / "echolot"

# Fewer nodes than this is a body such as `pass` or `return None`: alike by
# nature, and no rule to keep in one place.
MIN_NODES = 5

# Pairs kept apart on purpose, each with why.
APART: dict[frozenset[str], str] = {
    frozenset({"check.py:stop", "dig.py:stop"}):
        "the same line over a different module-level JOB: each stops its own job",
}


def _body(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = fn.body
    first = body[0] if body else None
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        body = body[1:]
    return body


def _copies(root: Path = PACKAGE) -> list[list[str]]:
    """Each group of top-level functions in two or more modules whose
    signature and body are the same."""
    seen: dict[str, list[str]] = defaultdict(list)
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        for fn in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = _body(fn)
            if sum(1 for stmt in body for _ in ast.walk(stmt)) < MIN_NODES:
                continue
            key = ast.dump(fn.args) + "".join(ast.dump(stmt) for stmt in body)
            seen[key].append(f"{module}:{fn.name}")
    return [names for names in seen.values()
            if len({n.split(":")[0] for n in names}) > 1 and frozenset(names) not in APART]


def test_no_helper_is_written_out_in_two_modules() -> None:
    copies = _copies()
    check("each helper is written once and imported where else it is needed "
          "(or kept apart in APART, with why)", not copies, copies)


def test_the_scan_finds_a_copy(tmp_path: Path) -> None:
    """The scan is no test if it finds nothing: a helper written out in two
    modules, under two names and with its own docstring, reads as a copy, and
    a body as small as `return None` does not."""
    helper = "    return f\"{n} {one if n == 1 else many}\"\n"
    (tmp_path / "a.py").write_text(
        "def _count(n, one, many):\n" + helper + "\n\ndef _none():\n    return None\n")
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "b.py").write_text(
        'def _counted(n, one, many):\n    """Plural."""\n' + helper
        + "\n\ndef _nothing():\n    return None\n")
    check("the copy is found, the bare `return None` is not",
          _copies(tmp_path) == [["a.py:_count", "pages/b.py:_counted"]], _copies(tmp_path))
