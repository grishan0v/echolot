"""An older echolot, and a `.claude/` layer a newer one wrote.

The manifest has named the echolot that wrote the layer for as long as there
has been a manifest, and nothing read the name back. In a team that commits
the layer, one person upgrades and runs `init`; a teammate still on the older
release runs `/echolot`. Every file the newer release changed matched its
hash in the manifest and not the older template — which is what `stale`
means — so `next` said `init`, the skill ran it by itself, the older files
went back in, the manifest took the older version, and `init` printed
"Layer updated."

The cases build that layer in a temporary directory — installed by this
echolot, then one file changed and one dropped, recorded the way another
release would have left them — and drive the CLI the way a person or an
agent does. How two versions are put in order is checked on its own at the
end, and against `packaging` where it is installed: echolot does not depend
on it, pytest does.

The closing self-check `init` runs is here too, for the other half of what
it got wrong about the project it had just installed into: it checked the
pinned trace_processor while `analyze` there would run the one local.yml
names. The self-check is stubbed wherever the question is which binary it
was handed — the real one, run over a trace_processor that does not exist,
would not answer it.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import json
import os
from pathlib import Path

import pytest

from echolot import layer, recorder, selftest
from echolot.layer import CLAUDE_DIR, GUIDE_DIR, LAYER_MANIFEST, version_key
from echolot.main import main
from echolot.state import next_kind, next_step, project_state
from tests.support import check

CONFIG = ("project:\n  package: com.example.app\n  process: com.example.app\n"
          "scenario:\n  name: coldStart\n")
CUSTOM = "/opt/custom/trace_processor_shell"


def run(project: Path, *argv: str) -> tuple[int, str]:
    """`echolot …` from inside `project`: exit code, and stdout and stderr."""
    out, err = io.StringIO(), io.StringIO()
    here = Path.cwd()
    os.chdir(project)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
    finally:
        os.chdir(here)
    return code, out.getvalue() + err.getvalue()


def _newer() -> str:
    """A release after the one running: the next major version."""
    return f"{int(recorder.version().split('.')[0]) + 1}.0.0"


def _as_left_by(project: Path, version: object) -> None:
    """The layer as another echolot left it, committed and pulled here.

    Installed by this echolot, then changed the way a different release
    would have written it: one file with other text, recorded in the
    manifest as what was installed, and one file that release does not ship.
    Against this echolot's template that reads as one stale file and one
    missing — exactly what a plain `init` brings up to date on its own.
    Around the layer, the rest of what `init` writes and this machine has
    not yet: that release's wording of the AGENTS.md section, a .gitignore
    without echolot's lines, no saved choice of agents.

    `version` goes into the manifest as it is; None takes the name out.
    """
    (project / ".git").mkdir(exist_ok=True)
    code, said = run(project, "init", "--no-input", "--no-doctor",
                     "--for", "claude,agents")
    check("the layer goes in", code == 0, said)

    root = project / ".claude"
    agent = root / "agents" / "perf-hunter.md"
    agent.write_text(agent.read_text(encoding="utf-8")
                     + "\n# as that release ships it\n", encoding="utf-8")
    layer.write_manifest(root, {"agents/perf-hunter.md": layer.sha(agent)})
    (root / "commands" / "echolot-reflect.md").unlink()
    manifest = json.loads((root / LAYER_MANIFEST).read_text(encoding="utf-8"))
    del manifest["files"]["commands/echolot-reflect.md"]
    if version is None:
        del manifest["echolot"]
    else:
        manifest["echolot"] = version
    (root / LAYER_MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n",
                                       encoding="utf-8")

    pointer = project / "AGENTS.md"
    pointer.write_text(pointer.read_text(encoding="utf-8").replace(
        "## Performance work: echolot",
        "## Performance work: echolot, as that release words it"), encoding="utf-8")
    (project / ".gitignore").write_text("build/\n", encoding="utf-8")
    (project / ".echolot" / "hosts.json").unlink()


def _tree(project: Path) -> dict[str, bytes | None]:
    """Every path under the project, and each file's bytes: "nothing written", checkable."""
    return {str(p.relative_to(project)): p.read_bytes() if p.is_file() else None
            for p in sorted(project.rglob("*"))}


def _changed(before: dict, after: dict) -> list[str]:
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


@pytest.fixture
def handed(monkeypatch) -> list:
    """The binary each self-check was handed, collected instead of run."""
    seen: list = []

    def run_checks(tp_binary=None):
        seen.append(tp_binary)
        return [("a check that holds", None)]

    monkeypatch.setattr(selftest, "run", run_checks)
    # The full `doctor` resolves the pinned binary itself, to print its path,
    # before the self-check: stubbed too, or an empty cache downloads it and
    # an offline one ends the run before the layer section (#277).
    monkeypatch.setattr("echolot.main.resolve_binary_path",
                        lambda tp_binary=None: tp_binary or "trace_processor_shell")
    return seen


# --- a newer layer: left alone, and said ------------------------------------

def test_an_older_init_writes_nothing_into_a_layer_a_newer_one_wrote(
        tmp_path: Path, handed: list) -> None:
    newer, this = _newer(), recorder.version()
    _as_left_by(tmp_path, newer)
    states = {r["file"]: r["state"] for r in layer.audit(tmp_path)["rows"]}
    check("file by file, it reads as what a plain init used to put back",
          states["agents/perf-hunter.md"] == "stale"
          and states["commands/echolot-reflect.md"] == "missing", states)

    before = _tree(tmp_path)
    for argv in (["init", "--no-input"],
                 ["init", "--no-input", "--all", "--for", "all"]):
        code, said = run(tmp_path, *argv)
        check(f"`echolot {' '.join(argv)}` refuses", code == 1, said)
        check("and says why, with both releases",
              f"written by echolot {newer}, a newer release than this {this}" in said,
              said)
        check("and how to upgrade", layer.UPGRADE in said, said)
        check("and never that the layer was updated", "Layer updated" not in said, said)
        check("and writes nothing — not the layer, the .gitignore, the pointer, "
              "or the saved choice of agents",
              _tree(tmp_path) == before, _changed(before, _tree(tmp_path)))
    check("the closing self-check is not run for a project it refused", handed == [],
          handed)


def test_status_and_doctor_say_upgrade_and_name_both_releases(
        tmp_path: Path, handed: list) -> None:
    newer, this = _newer(), recorder.version()
    _as_left_by(tmp_path, newer)

    st = project_state(tmp_path)
    check("the verdict is newer", st["layer_verdict"] == "newer", st["layer_verdict"])
    check("`next` is upgrade, ahead of the init the files would ask for",
          next_kind(st) == "upgrade", next_kind(st))
    check("and the step is the upgrade", layer.UPGRADE in next_step(st)
          and "echolot init" not in next_step(st), next_step(st))

    code, said = run(tmp_path, "status", "--next")
    check("`status --next` prints the word", code == 0 and said == "upgrade\n", said)

    code, said = run(tmp_path)
    rows = {ln.split()[0]: ln for ln in said.splitlines() if ln.strip()}
    check("`echolot` names both releases on the layer line",
          f"NEWER — written by echolot {newer}, a newer release than this {this}"
          in rows.get("layer", ""), said)
    check("and the upgrade", layer.UPGRADE in rows.get("layer", ""), said)
    check("and its next line is the upgrade", layer.UPGRADE in rows.get("next", ""),
          said)

    code, said = run(tmp_path, "doctor", "-q")
    lines = said.splitlines()
    check("`doctor -q` still exits on the self-check alone", code == 0, said)
    check("its layer line is the one `status` prints",
          len(lines) > 1 and lines[1] == layer.one_line(tmp_path)[1]
          and lines[1].startswith("layer: NEWER —"), said)

    code, said = run(tmp_path, "doctor")
    section = said.split("## The .claude/ layer in this project", 1)[-1]
    section = " ".join(section.split("## Self-check", 1)[0].split())
    check("the full doctor says the same",
          f"written by echolot {newer}, a newer release than this {this}" in section
          and layer.UPGRADE in section, section)
    check("and lists no file as stale against a template older than the layer",
          "stale" not in section and "perf-hunter.md" not in section, section)


# --- the same release, an older one, no name --------------------------------

def test_a_layer_this_release_wrote_is_current(tmp_path: Path) -> None:
    code, said = run(tmp_path, "init", "--no-input", "--no-doctor", "--for", "claude")
    check("init runs", code == 0, said)
    manifest = json.loads((tmp_path / ".claude" / LAYER_MANIFEST)
                          .read_text(encoding="utf-8"))
    check("the manifest names this release", manifest["echolot"] == recorder.version(),
          manifest)
    check("which holds nothing back", layer.ahead(tmp_path) is None)
    verdict, line = layer.one_line(tmp_path)
    check("the layer is current", verdict == "current"
          and line.startswith("layer: current ("), line)
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    check("and the route goes on to the hunt",
          next_kind(project_state(tmp_path)) == "hunt", next_kind(project_state(tmp_path)))


@pytest.mark.parametrize("written", ["0.1.0", "same", None],
                         ids=["an older release", "this release", "no name"])
def test_a_layer_no_newer_release_wrote_is_updated_as_before(
        tmp_path: Path, written: str | None) -> None:
    """Older, or the same number — a checkout between releases carries the
    last release's — or a manifest with no name in it: file by file."""
    _as_left_by(tmp_path, recorder.version() if written == "same" else written)
    check("stale, as before", layer.one_line(tmp_path)[0] == "stale",
          layer.one_line(tmp_path))
    check("and `next` is init", next_kind(project_state(tmp_path)) == "init",
          next_kind(project_state(tmp_path)))

    code, said = run(tmp_path, "init", "--no-input", "--no-doctor")
    check("init runs", code == 0, said)
    check("the changed file is brought up to date",
          "↑ .claude/agents/perf-hunter.md (updated)" in said, said)
    check("the dropped one is put back",
          "+ .claude/commands/echolot-reflect.md" in said, said)
    check("and it says so", "Layer updated." in said, said)
    manifest = json.loads((tmp_path / ".claude" / LAYER_MANIFEST)
                          .read_text(encoding="utf-8"))
    check("the manifest names this release now", manifest.get("echolot")
          == recorder.version(), manifest)
    check("and every file is current",
          {r["state"] for r in layer.audit(tmp_path)["rows"]} == {"current"},
          layer.audit(tmp_path)["rows"])


# --- a version that does not read -------------------------------------------

@pytest.mark.parametrize("written", ["banana", "unknown", "", "0.8.x",
                                     "0.8.0-SNAPSHOT", 8, ["0.8.0"]])
def test_a_version_that_does_not_read_is_left_alone(
        tmp_path: Path, written: object) -> None:
    """It could be newer, and guessing wrong in one direction undoes an upgrade."""
    _as_left_by(tmp_path, written)
    verdict, line = layer.one_line(tmp_path)
    check("left alone, like a newer one", verdict == "newer", verdict)
    check("and said as what it is, with the value",
          line.startswith("layer: VERSION UNREADABLE —") and repr(written) in line, line)
    check("with the upgrade, and the way out when that changes nothing",
          layer.UPGRADE in line and f"delete .claude/{LAYER_MANIFEST}" in line, line)
    check("`next` is upgrade", next_kind(project_state(tmp_path)) == "upgrade",
          next_kind(project_state(tmp_path)))

    before = _tree(tmp_path)
    code, said = run(tmp_path, "init", "--no-input", "--no-doctor")
    check("init refuses", code == 1, said)
    check("and writes nothing", _tree(tmp_path) == before,
          _changed(before, _tree(tmp_path)))


def test_the_way_out_of_an_unreadable_version_asks_before_overwriting(
        tmp_path: Path) -> None:
    """The line says: delete the manifest, and `init` keeps what differs.

    Followed to where it leads, because a way out that silently overwrote
    the files would be the rollback by another road.
    """
    _as_left_by(tmp_path, "banana")
    agent = tmp_path / ".claude" / "agents" / "perf-hunter.md"
    theirs = agent.read_bytes()
    (tmp_path / ".claude" / LAYER_MANIFEST).unlink()

    code, said = run(tmp_path, "init", "--no-input", "--no-doctor")
    check("init runs", code == 0, said)
    check("the file that differs is kept",
          agent.read_bytes() == theirs
          and "≠ .claude/agents/perf-hunter.md (already there and differs" in said, said)
    check("what was missing is added", "+ .claude/commands/echolot-reflect.md" in said,
          said)
    (tmp_path / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    check("and `--all` is a question for a person",
          next_kind(project_state(tmp_path)) == "init-force",
          next_kind(project_state(tmp_path)))


# --- which release may write over which -------------------------------------

@pytest.mark.parametrize("running,written,held", [
    ("0.7.0", "0.8.0", True),
    ("0.8.0", "0.7.0", False),
    ("0.9.0", "0.10.0", True),       # a string comparison says the opposite
    ("0.10.0", "0.9.0", False),
    ("0.8.0", "0.8.0rc1", False),    # the release updates its own candidate's layer
    ("0.8.0rc1", "0.8.0", True),     # and the candidate leaves the release's alone
    ("0.8.0rc1", "0.7.0", False),
    ("0.8.0.dev3", "0.8.0a1", True),
    ("0.8.0", "0.8", False),         # one version, two spellings
    ("0.8.0", "0.8.0+mine", False),  # a local build of the same release
])
def test_which_release_leaves_which_layer_alone(
        tmp_path: Path, monkeypatch, running: str, written: str, held: bool) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / LAYER_MANIFEST).write_text(
        json.dumps({"echolot": written, "files": {}}), encoding="utf-8")
    monkeypatch.setattr(recorder, "version", lambda: running)
    got = layer.ahead(tmp_path)
    check(f"{running} {'leaves' if held else 'may write over'} a layer {written} wrote",
          (got is not None) == held, got)
    if held:
        check("and says both", got["says"]
              == f"written by echolot {written}, a newer release than this {running}",
              got)


ORDER = ["0.7.0", "0.7.9", "0.8.0.dev1", "0.8.0a1.dev1", "0.8.0a1", "0.8.0b1",
         "0.8.0rc1", "0.8.0", "0.8.0.post1.dev1", "0.8.0.post1", "0.9.0", "0.10.0"]
SAME = [("0.8", "0.8.0"), ("0.8.0.0", "0.8.0"), ("0.8.0-rc.1", "0.8.0rc1"),
        ("0.8.0RC1", "0.8.0rc1"), ("0.8.0beta2", "0.8.0b2"), ("v0.8.0", "0.8.0"),
        ("0.8.0+mine.2", "0.8.0"), (" 0.8.0\n", "0.8.0")]


def test_versions_are_put_in_order_by_their_numbers() -> None:
    check("0.7.0 < 0.8.0 < 0.10.0",
          version_key("0.7.0") < version_key("0.8.0") < version_key("0.10.0"))
    check("which a string comparison gets wrong",
          "0.10.0" < "0.9.0" and version_key("0.10.0") > version_key("0.9.0"))
    keys = [version_key(v) for v in ORDER]
    wrong = [(a, b) for (a, ka), (b, kb) in itertools.pairwise(zip(ORDER, keys, strict=True))
             if not ka < kb]
    check("development, pre-, the release, post-: each after the one before",
          not wrong, wrong)
    for a, b in SAME:
        check(f"{a!r} is {b!r}", version_key(a) == version_key(b),
              (version_key(a), version_key(b)))


@pytest.mark.parametrize("value", [None, 8, "", "unknown", "banana", "0.8.x",
                                   "0.8.0-SNAPSHOT", "0.8.0 rc1", "1..0", ["0.8.0"]])
def test_what_does_not_read_as_a_version_has_no_place(value: object) -> None:
    check(f"{value!r} is not put anywhere in the order", version_key(value) is None,
          version_key(value))


def test_the_order_is_the_one_packaging_gives() -> None:
    """The claim in layer.py's comment, held to the library it names.

    A local label is dropped on the `packaging` side: it puts `0.8.0+mine`
    after `0.8.0`, and here a local build is the release it was built from.
    """
    version = pytest.importorskip("packaging.version")
    forms = [*ORDER, "0.8", "0.8.0.0", "0.8.0alpha2", "0.8.0c2", "0.8.0-rc.3",
             "0.8.0pre4", "0.8.0rc1.dev2", "0.8.0rc1.post1", "0.8.0-post2",
             "0.8.1.dev0", "1.0", "v1.0.1", "0.8.0+mine", "0.8.0.post", "0.8.0rc"]
    wrong = []
    for a, b in itertools.product(forms, repeat=2):
        pa = version.Version(version.Version(a).public)
        pb = version.Version(version.Version(b).public)
        ours = (version_key(a) > version_key(b)) - (version_key(a) < version_key(b))
        theirs = (pa > pb) - (pa < pb)
        if ours != theirs:
            wrong.append((a, b, ours, theirs))
    check("every pair in the same order", not wrong, wrong)


# --- what the agent is told -------------------------------------------------

@pytest.mark.parametrize("path", [CLAUDE_DIR / "skills" / "echolot" / "SKILL.md",
                                  GUIDE_DIR / "overview.md"],
                         ids=["SKILL.md", "guide overview"])
def test_the_upgrade_row_shows_the_line_and_stops(path: Path) -> None:
    rows = [ln for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.startswith("| `upgrade` |")]
    check("one row for `upgrade`", len(rows) == 1, rows)
    check("it shows the layer line to the human", "Show the `layer` line" in rows[0],
          rows[0])
    check("and stops", "and stop" in rows[0], rows[0])
    check("and does not send the agent to the init that refuses",
          "Do not run `echolot init`" in rows[0], rows[0])


def test_the_hunt_command_stops_on_a_newer_layer_before_its_init_route() -> None:
    """`/echolot hunt <words>` skips the door and reads `doctor -q` instead.

    The door routes `upgrade` to a stop. The hunt command read two things
    off the layer line — stale and naming `echolot init`: run it; naming
    `--all`: ask — and a NEWER line matched neither, so the hunt went on with
    the installed CLI against a layer written for a newer one. The stop has
    to come before the stale route: a VERSION UNREADABLE line names
    `echolot init` in its way out.
    """
    text = (CLAUDE_DIR / "commands" / "echolot-hunt.md").read_text(encoding="utf-8")
    env = " ".join(text[text.index("**Environment.**"):text.index("**Config.**")].split())
    stop, stale = env.find("`layer: NEWER`"), env.find("stale and names plain `echolot init`")
    check("the environment step names both lines",
          stop >= 0 and "`layer: VERSION UNREADABLE`" in env, env)
    check("with the upgrade, and a stop", layer.UPGRADE in env and "and stop" in env, env)
    check("and never init there", "do not run `echolot init` or `echolot init --all`"
          in env, env)
    check("before the stale route, which is still there", 0 <= stop < stale, (stop, stale))


# --- the binary init's closing check is handed ------------------------------

@pytest.mark.parametrize("flag,local,want,said", [
    ([], True, CUSTOM, "(custom binary from toolchain.tp_binary in {local})"),
    (["--tp-binary", "/opt/flag/tp"], True, "/opt/flag/tp",
     "(custom binary from --tp-binary)"),
    ([], False, None, None),
], ids=["local.yml", "the flag first", "the pin"])
def test_init_checks_the_binary_the_project_would_analyze_with(
        tmp_path: Path, handed: list, flag: list, local: bool, want: str | None,
        said: str | None) -> None:
    """The project `--into` names, not the directory `init` was typed in.

    Run from a directory with a config of its own that names another binary:
    what `analyze` would use in the project is the project's echolot.yml and
    the local.yml beside it.
    """
    project = tmp_path / "app"
    project.mkdir()
    (project / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    if local:
        (project / "local.yml").write_text(f"toolchain:\n  tp_binary: {CUSTOM}\n",
                                           encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "echolot.yml").write_text(CONFIG, encoding="utf-8")
    (elsewhere / "local.yml").write_text("toolchain:\n  tp_binary: /not/this/one\n",
                                         encoding="utf-8")

    code, out = run(elsewhere, "init", "--into", str(project), "--no-input",
                    "--for", "claude", *flag)
    check("init exits 0", code == 0, out)
    check("the self-check was handed the binary analyze would run there",
          handed == [want], handed)
    if said:
        words = said.format(local=project / "local.yml")
        check("and the first line says whose binary it was", words in out, out)
    else:
        check("and the first line names no custom binary", "custom binary" not in out,
              out)
