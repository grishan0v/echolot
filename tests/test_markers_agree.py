"""Every command that says whether temporary markers are left gives the same answer (#829).

`hunt` and `status` (through `hunt.recap`), the page in `echolot ui`
(`dig`), `reflect` (`facts.tree_check`) and `mark --remove` each read the
tree, and each used to read it by its own rule: `reflect` looked for the
prefix alone and only under `allowed` (#749, #838), `mark` compared
`./app/src/main` as written (#839), an empty `allowed` meant every place to
one and no place to another, and one reader ignored the project's own
prefix (#227). Now they all ask `mark.Markers`. This table holds them to
it: one tree per case, every reader, one expected answer.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import dig  # noqa: E402
from echolot import hunt as hunt_mod  # noqa: E402
from echolot import mark  # noqa: E402
from echolot.config import Config  # noqa: E402
from echolot.main import main as cli  # noqa: E402
from echolot.reflect import facts  # noqa: E402
from tests.support import check  # noqa: E402

APPLIED = ('        android.os.Trace.beginSection("{prefix}x") // echolot:mark\n'
           "        work()\n"
           "        android.os.Trace.endSection() // echolot:mark\n")
WRAPPED = "        try { parse() } finally { android.os.Trace.endSection() } // echolot:mark\n"


def kotlin(body: str) -> str:
    return "class A {\n    fun load() {\n" + body + "    }\n}\n"


@dataclass
class Case:
    """A tree, its config, and what every reader must say about it."""

    files: dict[str, str]
    config: str
    places: list[str]                 # files with anything of the markers in them
    kept: list[str] = field(default_factory=list)       # lines that go by hand, file:line
    outside: list[str] = field(default_factory=list)    # places outside `allowed`
    removed: int = 0                  # lines `mark --remove` deletes
    hand: list[str] = field(default_factory=list)       # markers added by hand, file:line


MAIN = "app/src/main/kotlin/com/example/app/screens/MainActivity.kt"
FOO = "app/src/main/kotlin/com/example/app/Foo.kt"
CORE = "core/src/main/kotlin/com/example/core/Cache.kt"
NATIVE = "app/src/main/cpp/bridge.cpp"
# A hunt behind a JNI call: the project's wrapper object, in a block opened
# for its scope (#1009).
SCOPED = ('void SetDir(const char* dir) {\n  {\n'
          '    ScopedTrace section("AGENTTMP_set_dir");\n'
          "    Store(dir);\n  }\n}\n")

CASES = {
    "a line --apply wrote": Case(
        {MAIN: kotlin(APPLIED.format(prefix="AGENTTMP_"))},
        'instrumentation:\n  allowed: ["app/src/main"]\n',
        places=[MAIN], removed=2),
    "an endSection wrapped by hand": Case(
        {FOO: kotlin(WRAPPED)},
        'instrumentation:\n  allowed: ["app/src/main"]\n',
        places=[FOO], kept=[f"{FOO}:3"]),
    "an allowed entry written ./": Case(
        {MAIN: kotlin(APPLIED.format(prefix="AGENTTMP_"))},
        'instrumentation:\n  allowed: ["./app/src/main"]\n',
        places=[MAIN], removed=2),
    "a file outside allowed": Case(
        {MAIN: kotlin(APPLIED.format(prefix="AGENTTMP_"))},
        "instrumentation:\n  allowed: app/src/main/kotlin/com/example/app/data\n",
        places=[MAIN], outside=[MAIN], removed=2),
    "an empty allowed list": Case(
        {CORE: kotlin(APPLIED.format(prefix="AGENTTMP_"))},
        "instrumentation:\n  allowed: []\n",
        places=[CORE], removed=2),
    "a project with its own prefix": Case(
        {MAIN: kotlin(APPLIED.format(prefix="PERF_")),
         # The default prefix is no marker here: a string the app writes.
         CORE: kotlin('        log("AGENTTMP_not_ours")\n')},
        'instrumentation:\n  allowed: ["app/src/main"]\n  temp_prefix: PERF_\n',
        places=[MAIN], removed=2),
    "a marker added by hand in C++": Case(
        {NATIVE: SCOPED},
        'instrumentation:\n  allowed: ["app/src/main"]\n',
        places=[NATIVE], hand=[f"{NATIVE}:3"]),
    "a tagged pair in C++ beside a Kotlin one": Case(
        {MAIN: kotlin(APPLIED.format(prefix="AGENTTMP_")),
         NATIVE: 'void f() {\n  ATrace_beginSection("AGENTTMP_f"); // echolot:mark\n'
                 "  g();\n  ATrace_endSection(); // echolot:mark\n}\n"},
        'instrumentation:\n  allowed: ["app/src/main/kotlin"]\n',
        places=[NATIVE, MAIN], kept=[f"{NATIVE}:2", f"{NATIVE}:4"], outside=[NATIVE], removed=2),
    "a clean tree": Case(
        {MAIN: kotlin("        work()\n")},
        'instrumentation:\n  allowed: ["app/src/main"]\n',
        places=[]),
}


def build(root: Path, case: Case) -> Config:
    for rel, text in case.files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    path = root / "echolot.yml"
    path.write_text(case.config, encoding="utf-8")
    return Config.load(path)


def state(cfg: Config) -> dict:
    """The part of `state.collect_state` the hunt line and the page read."""
    return {"config": {"temp_prefix": cfg.get("instrumentation.temp_prefix")}}


@pytest.mark.parametrize("name", list(CASES))
def test_every_reader_says_the_same(tmp_path: Path, name: str) -> None:
    case = CASES[name]
    root = tmp_path / "project"
    cfg = build(root, case)
    prefix = cfg.get("instrumentation.temp_prefix") or mark.DEFAULT_PREFIX

    # hunt and status: the lines under the open investigation.
    lines = hunt_mod.recap({"status": "open", "question": "q"}, state(cfg), root)
    said = "\n".join(lines)
    left_lines = [ln for ln in lines if "echolot:mark" in ln or "still in" in ln]
    check(f"{name}: hunt says something is left" if case.places else f"{name}: hunt says nothing",
          bool(left_lines) is bool(case.places), said)
    for place in [*case.kept, *case.hand]:
        check(f"{name}: hunt names {place}", place in said, said)

    # The page in `echolot ui`.
    page = dig._left(root, state(cfg))
    check(f"{name}: the page counts the same places",
          page["places"] == case.places and page["kept"] == case.kept, page)
    check(f"{name}: the page offers --remove for what it takes out",
          bool(page["removable"] or page["lines"]) is bool(case.removed), page)

    # reflect, read off the tree.
    tree = facts.tree_check(root, prefix, cfg.allowed)
    check(f"{name}: reflect names the same files",
          tree["files"] == case.places and tree["kept"] == case.kept, tree)
    check(f"{name}: reflect says which are outside allowed", tree["outside"] == case.outside, tree)

    # mark: where it may write, and what --remove takes out and leaves.
    outside = [p for p in case.places if not mark.Markers.of(root, cfg).allowed(p)]
    check(f"{name}: mark reads allowed the same way", outside == case.outside, outside)
    copy = tmp_path / "copy"
    shutil.copytree(root, copy)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli(["mark", "--remove", "--root", str(copy), "-c", str(copy / "echolot.yml")])
    check(f"{name}: mark --remove runs", code == 0, out.getvalue() + err.getvalue())
    removed = sum(int(ln.split(": ")[-1].split()[0]) for ln in out.getvalue().splitlines()
                  if ln.startswith("  - "))
    check(f"{name}: mark --remove takes out the lines the others count",
          removed == case.removed == page["lines"], (removed, out.getvalue()))
    kept = [ln.split(": ", 1)[0].strip().removeprefix("! ")
            for ln in out.getvalue().splitlines() if ln.startswith("  ! ")]
    check(f"{name}: and leaves the lines the others say go by hand",
          kept == case.kept + case.hand, kept)
    # After the press, every reader agrees on what is left: only the lines by hand.
    after = mark.Markers.of(copy, cfg).leftovers()
    check(f"{name}: what is left is what goes by hand", after["kept"] == case.kept
          and after["hand"] == case.hand and after["lines"] == 0, after)
