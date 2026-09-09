"""What the tool depends on, said in three places, checked against itself.

A runtime dependency is written down three times: in `[project.dependencies]`,
which is what pip installs; in `doctor`'s environment block, which is what a
person reads back when an answer looks wrong; and in the sample of that block
in `docs/determinism.md`, which is what somebody reads before they have run
anything. The first is the one a change touches. The other two are prose, and
prose goes stale silently — the same argument `test_docs.py` makes about the
counts, applied to the list those counts sit next to.

The second test here is about cost rather than agreement. `main` imports
`layer`, `layer` imports `hosts`, so anything imported at the top of any of
them is imported by `echolot status` and `echolot analyze` and every other
command that has no use for it. That is measured in the only way that cannot
be argued with: import the CLI in a clean process and ask which distributions
came along.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Everything `doctor` prints that is a fact about the machine rather than a
# distribution echolot depends on.
NOT_A_DEPENDENCY = {"python", "platform", "trace_processor"}


def _declared() -> set[str]:
    """Distribution names from `[project.dependencies]`.

    Read out of the text rather than through `tomllib`, which arrived in 3.11
    and would make this test quietly skip on the oldest Python the package
    claims to support.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^dependencies = \[(.*?)^\]", text, re.S | re.M)
    assert block, "pyproject.toml: no [project] dependencies list found"
    names = set()
    for spec in re.findall(r'"([^"]+)"', block.group(1)):
        names.add(re.split(r"[=<>!~;\[ ]", spec, maxsplit=1)[0])
    assert names, "pyproject.toml: the dependencies list came back empty"
    return names


def _reported() -> set[str]:
    """The labels down the left of `doctor`'s environment block.

    Taken from the syntax tree rather than by running `doctor`, which would
    want a trace_processor binary and a minute to use it. The list is built in
    one place and appended to once, so both shapes are collected.
    """
    tree = ast.parse((ROOT / "echolot/main.py").read_text(encoding="utf-8"))
    labels: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_doctor":
            for inner in ast.walk(node):
                rows = []
                if (isinstance(inner, ast.Assign)
                        and any(getattr(t, "id", "") == "facts" for t in inner.targets)
                        and isinstance(inner.value, ast.List)):
                    rows = inner.value.elts
                elif (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "append"
                        and getattr(inner.func.value, "id", "") == "facts"):
                    rows = inner.args
                for row in rows:
                    if isinstance(row, ast.Tuple) and isinstance(row.elts[0], ast.Constant):
                        labels.add(row.elts[0].value)
    assert labels, "echolot/main.py: cmd_doctor's `facts` list moved or changed shape"
    return labels


def _documented() -> set[str]:
    """The labels in the sample environment block in determinism.md."""
    text = (ROOT / "docs/determinism.md").read_text(encoding="utf-8")
    block = re.search(r"^## Environment\n\n(.*?)\n\n", text, re.S | re.M)
    assert block, "docs/determinism.md: the sample environment block moved or went away"
    keys = set(re.findall(r"^  (\S+) {2,}\S", block.group(1), re.M))
    assert keys, "docs/determinism.md: the sample environment block has no rows in it"
    return keys


def test_doctor_reports_every_dependency_that_is_installed() -> None:
    """A dependency `doctor` stays quiet about is one nobody can check.

    The block exists so that a version can be read back off a screenshot when
    an answer looks wrong. A dependency added to `pyproject.toml` and left out
    of here is invisible in exactly the situation the block was built for.
    """
    missing = _declared() - _reported()
    assert not missing, (
        f"in [project.dependencies] and absent from doctor's environment "
        f"block: {sorted(missing)}"
    )


def test_the_documented_environment_block_matches_the_real_one() -> None:
    """The sample in the docs is read by people who have not run `doctor` yet.

    So it is a claim about the tool in the same way a sentence is, and it goes
    stale the same way: a row is added to the code and the example keeps
    showing the output of an older version.
    """
    reported, documented = _reported(), _documented()
    assert documented == reported, (
        f"doctor prints {sorted(reported)}, docs/determinism.md shows "
        f"{sorted(documented)}"
    )


def test_the_documented_block_lists_only_real_dependencies() -> None:
    """Guards the pair above against agreeing on something that is wrong.

    Both lists are read out of files, so both can be edited to match each
    other while naming a distribution that is not installed. This is the third
    corner: everything in the block that is not a fact about the machine has
    to appear in `[project.dependencies]`.
    """
    claimed = _reported() - NOT_A_DEPENDENCY
    stray = claimed - _declared()
    assert not stray, (
        f"doctor's environment block names {sorted(stray)}, which "
        f"[project.dependencies] does not install"
    )


def test_importing_the_cli_pulls_in_one_distribution() -> None:
    """Every command pays for whatever the import chain drags along.

    `questionary` went in at the top of `hosts.py`, which `layer` imports and
    `main` imports after it, and brought prompt_toolkit with it: 238 ms to
    import the CLI against 104 ms with the import moved inside the one
    function that asks a question. Nothing failed, and nothing would have.

    So the allowed set is written out here and kept short deliberately. A new
    name in it should be a decision somebody made on purpose, and this test
    turning red is what makes it one.

    What is measured is the difference the import makes, taken across it
    rather than read off the end. An interpreter arrives with things in
    `sys.modules` that nobody asked for — on 3.10 a setuptools `.pth` file
    runs before the first line of the program, and a `sitecustomize` or a
    Cython shim can appear on any version. Those cost their time whether or
    not echolot exists, and the first version of this test failed on 3.10 for
    reporting one of them.

    Counted by distribution rather than by module name, so that a package
    whose import name and installed name differ still lines up with what
    `[project.dependencies]` says.
    """
    allowed = {"PyYAML"}
    probe = (
        "import sys\n"
        "from importlib.metadata import packages_distributions\n"
        "owner = packages_distributions()\n"
        "before = {n.partition('.')[0] for n in sys.modules}\n"
        "import echolot.main\n"
        "added = {n.partition('.')[0] for n in sys.modules} - before\n"
        "print(' '.join(sorted({d for t in added for d in owner.get(t, ())\n"
        "                       if d != 'echolot'})))\n"
    )
    run = subprocess.run([sys.executable, "-c", probe], cwd=ROOT,
                         capture_output=True, text=True)
    assert run.returncode == 0, f"importing the CLI failed:\n{run.stderr}"
    pulled = set(run.stdout.split())
    assert pulled <= allowed, (
        f"importing the CLI now pulls in {sorted(pulled - allowed)}. If that "
        f"is deliberate, add it here; if it is only needed by one command, "
        f"import it inside that command instead"
    )
