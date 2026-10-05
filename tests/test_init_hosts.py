"""`init` and the choice of clients, where it installed what it should not or
misread what it had written.

The Codex advice before a choice, `--for all` beside the plugin, a manifest
written on another system, a checkout with CRLF line endings, a section
updated inside a file with its own line endings and bytes, a section an old
release wrote, and a hand-edited choice.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import codex, hosts, layer, recorder  # noqa: E402
from echolot.main import main  # noqa: E402
from tests.support import check  # noqa: E402


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with recorder.isolated(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def test_before_a_choice_the_codex_advice_installs_no_claude_layer(tmp_path: Path) -> None:
    check("detection's Claude Code is left out, AGENTS.md stands in",
          codex.init_command(["claude", "codex"], tmp_path) == "echolot init --for agents,codex")
    (tmp_path / ".echolot").mkdir()
    (tmp_path / ".echolot" / "hosts.json").write_text('{"hosts": ["claude"]}', encoding="utf-8")
    check("a project that chose Claude Code keeps it",
          codex.init_command(["claude"], tmp_path) == "echolot init --for claude,codex")


def test_all_never_chooses_the_layer_and_the_plugin_together() -> None:
    keys = [h.key for h in hosts.parse("all")]
    check("all is everything but the plugin", "claude" in keys and "plugin" not in keys, keys)
    keys = [h.key for h in hosts.parse("all", ["plugin"])]
    check("and beside the plugin, everything but Claude Code",
          "plugin" in keys and "claude" not in keys, keys)


def test_a_choice_of_both_is_refused(tmp_path: Path) -> None:
    code, said = _run("init", "--into", str(tmp_path), "--for", "claude,plugin")
    check("refused, with why", code == 2 and "load them twice" in said, said)
    check("and nothing installed", not (tmp_path / ".claude").exists())


def test_the_plugin_project_s_hint_names_the_rest(tmp_path: Path) -> None:
    code, said = _run("init", "--into", str(tmp_path), "--for", "plugin")
    check("init ran", code == 0, said)
    named = said.split(" adds the rest")[0].rsplit("echolot init --for ", 1)[-1].strip("`")
    check("the hint names clients, the plugin among them and Claude Code not",
          "--for all" not in said and "plugin" in named.split(",")
          and "claude" not in named.split(","), named)


def _layer(tmp_path: Path) -> Path:
    code, said = _run("init", "--into", str(tmp_path), "--for", "claude")
    check("the layer is in", code == 0, said)
    return tmp_path / ".claude"


def _make_stale(root: Path) -> None:
    """An older text, untouched since install: what a package update leaves."""
    skill = root / "skills" / "echolot" / "SKILL.md"
    skill.write_text("an older text\n", encoding="utf-8")
    manifest = json.loads((root / layer.LAYER_MANIFEST).read_text(encoding="utf-8"))
    manifest["files"]["skills/echolot/SKILL.md"] = layer.sha(skill)
    (root / layer.LAYER_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")


def _skill_state(project: Path) -> str:
    rows = {r["file"]: r["state"] for r in layer.audit(project)["rows"]}
    return rows["skills/echolot/SKILL.md"]


def test_a_manifest_from_windows_reads_on_a_mac(tmp_path: Path) -> None:
    root = _layer(tmp_path)
    _make_stale(root)
    manifest = json.loads((root / layer.LAYER_MANIFEST).read_text(encoding="utf-8"))
    manifest["files"] = {k.replace("/", "\\"): v for k, v in manifest["files"].items()}
    (root / layer.LAYER_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    check("an untouched old file is stale, not edited", _skill_state(tmp_path) == "stale",
          layer.audit(tmp_path))


def test_crlf_line_endings_are_not_an_edit(tmp_path: Path) -> None:
    root = _layer(tmp_path)
    for f in root.rglob("*.md"):
        f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
    check("a CRLF checkout of the layer is current", _skill_state(tmp_path) == "current")
    _make_stale(root)
    skill = root / "skills" / "echolot" / "SKILL.md"
    skill.write_bytes(skill.read_bytes().replace(b"\n", b"\r\n"))
    check("and an untouched old file in CRLF is stale", _skill_state(tmp_path) == "stale")


def test_an_update_leaves_the_rest_of_the_file_as_it_was(tmp_path: Path) -> None:
    host = hosts.BY_KEY["gemini"]
    old = host.render().replace("echolot guide", "echolot guide (old)", 1)
    before = (b"# Our rules\r\nCaf\xe9 is spelled in cp1252 here.\r\n\r\n"
              + old.replace("\n", "\r\n").encode("utf-8") + b"\r\nOur own last line.\r\n")
    (tmp_path / "GEMINI.md").write_bytes(before)
    what, path = hosts.write_stub(tmp_path, host)
    after = path.read_bytes()
    check("updated", what == "updated", what)
    check("CRLF throughout, and the cp1252 byte kept",
          b"\r\n" in after and b"\n" not in after.replace(b"\r\n", b"")
          and b"Caf\xe9 is spelled" in after and after.endswith(b"Our own last line.\r\n"),
          after[:120])


def test_a_section_0_4_0_wrote_is_migrated() -> None:
    for key in ("agents", "gemini", "cursor", "copilot"):
        earlier = hosts._CURSOR_0_4_0 if key == "cursor" else hosts._BODY_0_4_0
        check(f"{key}: untouched", hosts._untouched_without_an_end(earlier, hosts.BY_KEY[key]))


def test_a_hand_edited_choice_does_not_crash(tmp_path: Path) -> None:
    (tmp_path / ".echolot").mkdir()
    (tmp_path / ".echolot" / "hosts.json").write_text('{"hosts": [["claude"], "codex"]}',
                                                     encoding="utf-8")
    check("the key it can read is kept", hosts.load_choice(tmp_path) == ["codex"])
