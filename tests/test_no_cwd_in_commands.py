"""No command decides its project from the working directory (#830).

`main()` resolves the project once (`echolot/project.py`) and every command
reads that answer: `args.where`, through `_where(args)`. A `Path.cwd()` in a
command is a second answer, the kind that had `hunt` keep its investigation
in the project and its run log in `app/` (#835). The resolvers it replaced
stay gone too.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

PACKAGE = Path(__file__).resolve().parent.parent / "echolot"
GONE = {"_project_root", "project_of", "_nearest_config", "_config_above",
        "_project_and_config", "_mark_root", "_mark_config", "_probe_config",
        "_doctor_config"}


def _is_cwd(node: ast.AST) -> bool:
    """`Path.cwd()` or `os.getcwd()`."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and (node.func.value.id, node.func.attr) in {("Path", "cwd"), ("os", "getcwd")})


def test_no_command_reads_the_working_directory() -> None:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name.startswith("cmd_"):
                found += [f"{path.relative_to(PACKAGE)}:{node.lineno} {fn.name}"
                          for node in ast.walk(fn) if _is_cwd(node)]
    check("no Path.cwd() in a cmd_*: `_where(args).here` is where it ran", not found, found)


def test_the_old_resolvers_are_gone() -> None:
    left = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        left += [f"{path.relative_to(PACKAGE)}:{fn.lineno} {fn.name}" for fn in ast.walk(tree)
                 if isinstance(fn, ast.FunctionDef) and fn.name in GONE]
    check("each answered which project by its own rule", not left, left)
