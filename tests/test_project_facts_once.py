"""Every fact about the project that more than one reader needs is worked out
once, in `state` (#991).

`state.next_kind` already decides the next step in one place; the facts under
it were still looked up again by whoever needed them. The start page and its
check found an investigation and its filed reports on their own, the agent's
panel loaded `hunt.json` and counted the leftover markers itself, and
`state.by_hand` and `hunt` had two rules for "recorded outside the
investigation" until they drifted (#720). The readers below read those facts
from `state` and never derive them again.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import check  # noqa: E402

PACKAGE = Path(__file__).resolve().parent.parent / "echolot"
# The commands' and the pages' code that reads the project's state.
READERS = ["ready.py", "check.py", "dig.py", "ui.py", "project_setup.py",
           *(f"pages/{p.name}" for p in sorted((PACKAGE / "pages").glob("*.py")))]
# What `state` works out from `hunt` and `mark`: the investigation, where it
# files its copies, the reports it filed, whether the traces were recorded
# outside it, and the markers left in the code.
HUNT_FACTS = {"load", "find", "history", "home", "archived_reports", "recorded_outside"}
MARK_FACTS = {"Markers"}


def _derived(tree: ast.AST) -> list[tuple[int, str]]:
    """Where a module works one of those facts out itself: a call into
    `hunt` for one, or a `Markers` of its own."""
    hunt_names = {"hunt_mod"}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] in {"hunt", "mark"}:
            facts = HUNT_FACTS if node.module.endswith("hunt") else MARK_FACTS
            dots = "." * node.level
            found += [(node.lineno, f"from {dots}{node.module} import {a.name}") for a in node.names
                      if a.name in facts]
        elif isinstance(node, ast.ImportFrom):
            hunt_names |= {a.asname or a.name for a in node.names if a.name == "hunt"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id in hunt_names and node.attr in HUNT_FACTS:
            found.append((node.lineno, f"{node.value.id}.{node.attr}"))
        elif node.attr in MARK_FACTS:
            found.append((node.lineno, f"{node.value.id}.{node.attr}"))
    return found


def test_the_readers_read_the_facts_from_state() -> None:
    found = []
    for rel in READERS:
        tree = ast.parse((PACKAGE / rel).read_text(encoding="utf-8"))
        found += [f"echolot/{rel}:{line} {what}" for line, what in _derived(tree)]
    check("no reader looks up the investigation, its reports or the markers itself: "
          "`state` has them", not found, found)


def test_the_scan_sees_a_reader_that_does() -> None:
    """The scan above passes on nothing as well as on clean code."""
    tree = ast.parse("from . import hunt as hunt_mod\nfrom .mark import Markers\n"
                     "h = hunt_mod.load(p)\nr = hunt_mod.archived_reports(p, h)\n")
    said = [what for _, what in _derived(tree)]
    check("each one is caught", said == ["from .mark import Markers", "hunt_mod.load",
                                         "hunt_mod.archived_reports"], said)
