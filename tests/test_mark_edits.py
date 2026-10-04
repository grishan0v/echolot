#!/usr/bin/env python3
"""`mark`, against the shapes real sources and manifests come in.

The promise of `--apply` and `--remove` is two sentences: every line
`--apply` puts in is a whole line of its own, between two of the project's,
and `--remove` takes out exactly those lines — so a file goes through both
and comes back byte for byte. The self-check tried it on one tidy tree,
written the way this tool's author writes Kotlin, and an audit broke it with
the first shapes it tried: `setContent { AppTheme {` on one line, a comment
after an `{`, a file with CRLF line endings.

So the sources here are generated as well as written out. Every brace line
gets a tail or a head drawn from what real code puts there — nothing, blanks,
a comment, code — in Kotlin and in Java, with `\\n` or `\\r\\n`, and the
properties are the promise itself: the bytes come back, the plan refuses
exactly the blocks whose braces share a line with code, and what it does not
refuse is marked right inside the braces.

The rest is what `mark` decides before it edits anything, and each of those
was wrong on a shape nobody had written a test for: which activity is the
launcher, which paths `instrumentation.allowed` covers, which threads already
have a name.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import mark  # noqa: E402
from echolot.main import main as cli  # noqa: E402
from echolot.reflect import facts  # noqa: E402
from tests.support import check  # noqa: E402



def _no_config(root):
    """A config with nothing in it: no guard, no package, the default prefix.

    Named rather than left out, so that an echolot.yml in the directory the
    suite runs from is not read; a `-c` that names no file is refused.
    """
    path = root / "empty.yml"
    path.write_text("", encoding="utf-8")
    return str(path)

def run(*argv: str) -> tuple[int, str, str]:
    """A command, with what it printed on each stream."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli(list(argv))
    return code, out.getvalue(), err.getvalue()


FILTER = ('<intent-filter><action android:name="android.intent.action.MAIN" />'
          '<category android:name="android.intent.category.LAUNCHER" /></intent-filter>')


def manifest(*activities: str, application: str = ".App") -> str:
    return ('<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
            f'  <application android:name="{application}">\n'
            + "".join(f"    {a}\n" for a in activities)
            + "  </application>\n</manifest>\n")


LAUNCHER = f'<activity android:name=".MainActivity" android:exported="true">{FILTER}</activity>'


def write(root: Path, rel: str, text: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, str):
        text = text.encode("utf-8")
    path.write_bytes(text)
    return path


# --- the round trip, over generated shapes ----------------------------------

# What a line may carry after a block's `{`, and whether that is code.
OPEN_TAILS = [
    ("", False),
    ("   ", False),
    (" // the entry point", False),
    (" /* kept */", False),
    (" /* a */ // b", False),
    (" init()", True),
    (' Log.d("tag", "{")', True),
]
# What a line may carry before a block's `}`.
CLOSE_HEADS = [
    ("", False),
    ("/* done */ ", False),
    ("init() ", True),
]
# What a setContent body may be: one line, a lambda on one line, a lambda
# over three, a brace inside a string, a blank line first, a comment first.
BODIES = [
    ("Home()",),
    ("AppTheme { Home() }",),
    ("AppTheme {", "    Home()", "}"),
    ('Text("}")',),
    ("", "Home()"),
    ("// the root", "Home()"),
]


@dataclass(frozen=True)
class Shape:
    java: bool = False
    allman: bool = False            # Java: the `{` on a line of its own
    newline: str = "\n"
    indent: str = "    "
    final_newline: bool = True
    app_flat: bool = False          # the Application's onCreate on one line
    app_open: tuple[str, bool] = OPEN_TAILS[0]
    app_close: tuple[str, bool] = CLOSE_HEADS[0]
    on_open: tuple[str, bool] = OPEN_TAILS[0]
    on_close: tuple[str, bool] = CLOSE_HEADS[0]
    sc_open: tuple[str, bool] = OPEN_TAILS[0]
    sc_close: tuple[str, bool] = CLOSE_HEADS[0]
    content: str = "lines"          # lines | flat | nested
    body: tuple[str, ...] = BODIES[0]


def _code(option: tuple[str, bool], java: bool) -> str:
    """The option's text, with the semicolon Java wants after a statement."""
    text, is_code = option
    if not (java and is_code):
        return text
    return text.rstrip() + ";" + (" " if text.endswith(" ") else "")


def sources(s: Shape) -> dict[str, str]:
    """The Application and the launcher Activity, in the shape's words."""
    i, j = s.indent, s.java

    def tail(o):
        return _code(o, j)

    def opening(signature: str, o) -> list[str]:
        """A signature and its `{`, on one line or, Allman style, on two."""
        if j and s.allman:
            return [f"{i}{signature}", f"{i}{{{tail(o)}"]
        return [f"{i}{signature} {{{tail(o)}"]

    if j:
        app = (["package com.example.app;", "",
                "public class App extends Application {", f"{i}@Override"]
               + ([f"{i}public void onCreate() {{ super.onCreate(); }}"] if s.app_flat else
                  opening("public void onCreate()", s.app_open)
                  + [f"{i}{i}super.onCreate();", f"{i}{tail(s.app_close)}}}"])
               + ["}"])
        act = (["package com.example.app;", "",
                "public class MainActivity extends Activity {", f"{i}@Override"]
               + opening("protected void onCreate(Bundle savedInstanceState)", s.on_open)
               + [f"{i}{i}super.onCreate(savedInstanceState);",
                  f"{i}{i}setContentView(R.layout.main);",
                  f"{i}{tail(s.on_close)}}}",
                  "}"])
        ext = "java"
    else:
        app = (["package com.example.app", "", "class App : Application() {"]
               + ([f"{i}override fun onCreate() {{ super.onCreate() }}"] if s.app_flat else
                  opening("override fun onCreate()", s.app_open)
                  + [f"{i}{i}super.onCreate()", f"{i}{tail(s.app_close)}}}"])
               + ["}"])
        if s.content == "flat":
            content = [f"{i}{i}setContent {{ {' '.join(ln.strip() for ln in s.body)} }}"]
        else:
            nested = " AppTheme {" if s.content == "nested" else ""
            content = ([f"{i}{i}setContent {{{nested}{tail(s.sc_open)}"]
                       + [f"{i}{i}{i}{line.replace('    ', i)}" if line else ""
                          for line in s.body]
                       + [f"{i}{i}{'} ' if nested else ''}{tail(s.sc_close)}}}"])
        act = (["package com.example.app", "",
                "class MainActivity : ComponentActivity() {"]
               + opening("override fun onCreate(savedInstanceState: Bundle?)", s.on_open)
               + [f"{i}{i}super.onCreate(savedInstanceState)"]
               + content
               + [f"{i}{tail(s.on_close)}}}", "}"])
        ext = "kt"
    end = s.newline if s.final_newline else ""
    return {f"app/src/main/{ext}/com/example/app/App.{ext}": s.newline.join(app) + end,
            f"app/src/main/{ext}/com/example/app/MainActivity.{ext}": s.newline.join(act) + end}


def expected(s: Shape) -> dict[str, bool]:
    """Which blocks can take a pair: none on one line, none whose braces share
    a line with code."""
    out = {"app_oncreate": not s.app_flat and not s.app_open[1] and not s.app_close[1],
           "activity_oncreate": not s.on_open[1] and not s.on_close[1]}
    if not s.java:
        out["set_content"] = (s.content == "lines"
                              and not s.sc_open[1] and not s.sc_close[1])
    return out


def _marked_inside(text: str, block, marker: str) -> bool:
    """The begin line right under the `{`'s line, the end right over the `}`'s."""
    if block is None or block[1] is None or block[2] is None:
        return False
    open_at, close_at = block[1], block[2]
    lines = text.split("\n")
    first, last = mark.line_of(text, open_at), mark.line_of(text, close_at)
    begin, end = lines[first], lines[last - 2]
    return (mark.is_applied_line(begin) and f'beginSection("{marker}")' in begin
            and mark.is_applied_line(end) and "endSection()" in end)


shapes = st.builds(
    Shape,
    java=st.booleans(),
    allman=st.booleans(),
    newline=st.sampled_from(["\n", "\r\n"]),
    indent=st.sampled_from(["    ", "\t"]),
    final_newline=st.booleans(),
    app_flat=st.booleans(),
    app_open=st.sampled_from(OPEN_TAILS),
    app_close=st.sampled_from(CLOSE_HEADS),
    on_open=st.sampled_from(OPEN_TAILS),
    on_close=st.sampled_from(CLOSE_HEADS),
    sc_open=st.sampled_from(OPEN_TAILS),
    sc_close=st.sampled_from(CLOSE_HEADS),
    content=st.sampled_from(["lines", "flat", "nested"]),
    body=st.sampled_from(BODIES),
)

# The shapes this exists for — the audit's, and the refusals that came before
# them — each named, so no seed can leave one out.
AUDIT_SHAPES = {
    "code after the brace": Shape(content="nested"),
    "a comment after the brace": Shape(on_open=(" // entry point", False)),
    "CRLF": Shape(newline="\r\n"),
    "CRLF, a comment, no newline at the end": Shape(
        newline="\r\n", on_open=(" // entry point", False), final_newline=False),
    "one-line bodies": Shape(content="flat", app_flat=True),
    "nested lambdas over several lines": Shape(body=BODIES[2]),
    "Java with semicolons": Shape(java=True, on_open=(" // entry point", False)),
    "Java, CRLF, code before the brace": Shape(
        java=True, newline="\r\n", on_close=("init() ", True)),
    "Java, the brace on its own line": Shape(java=True, allman=True, app_flat=True),
}


@example(AUDIT_SHAPES["code after the brace"])
@example(AUDIT_SHAPES["a comment after the brace"])
@example(AUDIT_SHAPES["CRLF"])
@example(AUDIT_SHAPES["CRLF, a comment, no newline at the end"])
@example(AUDIT_SHAPES["one-line bodies"])
@example(AUDIT_SHAPES["nested lambdas over several lines"])
@example(AUDIT_SHAPES["Java with semicolons"])
@example(AUDIT_SHAPES["Java, CRLF, code before the brace"])
@example(AUDIT_SHAPES["Java, the brace on its own line"])
@given(shapes)
def test_apply_then_remove_gives_every_byte_back(shape):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write(root, "app/src/main/AndroidManifest.xml", manifest(LAUNCHER))
        paths = {rel: write(root, rel, text) for rel, text in sources(shape).items()}
        before = {rel: p.read_bytes() for rel, p in paths.items()}

        pl = mark.plan(root)
        want = expected(shape)
        got = {p.kind: p.applicable for p in pl.proposals if p.kind in want}
        check("the plan refuses exactly the blocks with code on a brace line",
              got == want, (got, want, [(p.kind, p.reason) for p in pl.proposals]))
        for p in pl.proposals:
            check("every refusal says why", p.applicable or p.reason, p)

        done, unreadable = mark.apply(root, pl)
        check("every file was read", unreadable == [], unreadable)
        marked = sorted(m for _, ms in done for m in ms)
        check("apply marks what the plan called applicable, and nothing else",
              marked == sorted(p.marker for p in pl.proposals if p.applicable),
              (marked, [(p.marker, p.applicable) for p in pl.proposals]))

        for rel, p in paths.items():
            text = p.read_bytes().decode("utf-8")
            if shape.newline == "\r\n":
                check(f"{rel}: every line still ends in CRLF, the new ones too",
                      text.count("\r\n") == text.count("\n"), repr(text))
            for q in pl.proposals:
                if q.file != rel or not q.applicable:
                    continue
                block = (mark.find_lambda(text, mark._SET_CONTENT)
                         if q.kind == "set_content" else mark.find_on_create(text))
                check(f"{q.marker} sits right inside its braces",
                      _marked_inside(text, block, q.marker), text)

        touched, kept = mark.remove(root)
        check("nothing tagged is left behind", kept == [], kept)
        after = {rel: p.read_bytes() for rel, p in paths.items()}
        check("every file comes back byte for byte", after == before,
              {rel: (before[rel], after[rel]) for rel in paths if before[rel] != after[rel]})


# --- the audit's shapes, spelled out -------------------------------------------

def _tree(root: Path, activity: str, *, ext: str = "kt", newline: str = "\n") -> Path:
    write(root, "app/src/main/AndroidManifest.xml", manifest(LAUNCHER, application=".App"))
    write(root, f"app/src/main/{ext}/com/example/app/App.{ext}",
          "class App : Application()\n" if ext == "kt" else "public class App extends Application {}\n")
    return write(root, f"app/src/main/{ext}/com/example/app/MainActivity.{ext}",
                 activity.replace("\n", newline))


def test_code_after_the_brace_is_refused_and_stays_on_its_line(tmp_path):
    """`setContent { AppTheme {` became a begin line with `AppTheme {` behind
    its tag — commented out, so the file stopped compiling — and `--remove`
    could not take that line out again."""
    path = _tree(tmp_path, (
        "class MainActivity : ComponentActivity() {\n"
        "    override fun onCreate(savedInstanceState: Bundle?) {\n"
        "        super.onCreate(savedInstanceState)\n"
        "        setContent { AppTheme {\n"
        "            Home()\n"
        "        } }\n"
        "    }\n"
        "}\n"))
    before = path.read_bytes()
    pl = mark.plan(tmp_path)
    sc = next(p for p in pl.proposals if p.kind == "set_content")
    check("setContent is refused", not sc.applicable, sc)
    check("naming what to move", "code follows the `{`" in sc.reason, sc.reason)
    check("onCreate around it is not", next(
        p for p in pl.proposals if p.kind == "activity_oncreate").applicable, pl.proposals)

    mark.apply(tmp_path, pl)
    lines = path.read_text(encoding="utf-8").splitlines()
    check("the line with the code is untouched", "        setContent { AppTheme {" in lines, lines)
    check("no code sits behind a tag",
          all(mark.is_applied_line(ln) for ln in lines if mark.TAG in ln), lines)
    mark.remove(tmp_path)
    check("and the file comes back", path.read_bytes() == before, path.read_text(encoding="utf-8"))


def test_code_before_the_closing_brace_is_refused_too(tmp_path):
    path = _tree(tmp_path, (
        "class MainActivity : ComponentActivity() {\n"
        "    override fun onCreate(savedInstanceState: Bundle?) {\n"
        "        super.onCreate(savedInstanceState)\n"
        "        init() }\n"
        "}\n"))
    pl = mark.plan(tmp_path)
    oc = next(p for p in pl.proposals if p.kind == "activity_oncreate")
    check("refused", not oc.applicable, oc)
    check("with the reason", "code comes before the `}`" in oc.reason, oc.reason)
    before = path.read_bytes()
    check("and nothing is applied", mark.apply(tmp_path, pl) == ([], []))
    check("so nothing changed", path.read_bytes() == before)


def test_a_comment_after_the_brace_stays_where_it_was(tmp_path):
    """The comment used to land behind the begin line's tag, and `--remove`
    then left that begin in the file with no end."""
    path = _tree(tmp_path, (
        "class MainActivity : ComponentActivity() {\n"
        "    override fun onCreate(savedInstanceState: Bundle?) { // entry point\n"
        "        super.onCreate(savedInstanceState)\n"
        "    }\n"
        "}\n"))
    before = path.read_bytes()
    pl = mark.plan(tmp_path)
    check("applicable", all(p.applicable for p in pl.proposals
                            if p.kind == "activity_oncreate"), pl.proposals)
    mark.apply(tmp_path, pl)
    lines = path.read_text(encoding="utf-8").splitlines()
    check("the comment is still on the brace's line",
          lines[1] == "    override fun onCreate(savedInstanceState: Bundle?) { // entry point", lines)
    check("and the begin line is under it",
          lines[2] == '        android.os.Trace.beginSection("AGENTTMP_activity_oncreate") '
                      "// echolot:mark", lines)
    mark.remove(tmp_path)
    after = path.read_text(encoding="utf-8")
    check("no begin is left without its end", "beginSection" not in after, after)
    check("the bytes are back", path.read_bytes() == before, after)


def test_a_crlf_file_comes_back_crlf(tmp_path):
    """`read_text` and `write_text` turned every `\\r\\n` into `\\n` on the way
    through: the whole file changed, for a command promising no change."""
    path = _tree(tmp_path, (
        "class MainActivity : ComponentActivity() {\n"
        "    override fun onCreate(savedInstanceState: Bundle?) {\n"
        "        super.onCreate(savedInstanceState)\n"
        "        setContent {\n"
        "            Home()\n"
        "        }\n"
        "    }\n"
        "}\n"), newline="\r\n")
    before = path.read_bytes()
    done, _ = mark.apply(tmp_path, mark.plan(tmp_path))
    check("marked", done, done)
    applied = path.read_bytes()
    check("the inserted lines end in CRLF like the rest",
          applied.count(b"\r\n") == applied.count(b"\n") == before.count(b"\n") + 4, applied)
    mark.remove(tmp_path)
    check("byte for byte", path.read_bytes() == before, path.read_bytes())


def test_java_gets_its_semicolons_and_its_bytes_back(tmp_path):
    path = _tree(tmp_path, (
        "public class MainActivity extends Activity {\n"
        "    @Override\n"
        "    protected void onCreate(Bundle savedInstanceState) {\n"
        "        super.onCreate(savedInstanceState);\n"
        "        setContentView(R.layout.main);\n"
        "    }\n"
        "}\n"), ext="java")
    before = path.read_bytes()
    mark.apply(tmp_path, mark.plan(tmp_path))
    tagged = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if mark.TAG in ln]
    check("a pair, each a statement", tagged == [
        'android.os.Trace.beginSection("AGENTTMP_activity_oncreate"); // echolot:mark',
        "android.os.Trace.endSection(); // echolot:mark"], tagged)
    mark.remove(tmp_path)
    check("byte for byte", path.read_bytes() == before)


def test_remove_names_the_tagged_lines_it_leaves(tmp_path):
    """A tag on a line of code is left alone — deleting the line would take
    the code — and said, with file and line. It used to be passed over and
    the command printed that no tagged lines were found at all."""
    rel = "app/src/main/kotlin/A.kt"
    path = write(tmp_path, rel, (
        "class A {\n"
        "    fun go() {\n"
        '        android.os.Trace.beginSection("AGENTTMP_x") // echolot:mark AppTheme {\n'
        "        work() // echolot:mark\n"
        "    }\n"
        "}\n"))
    before = path.read_bytes()
    touched, kept = mark.remove(tmp_path)
    check("nothing removed", touched == [], touched)
    check("both lines named, with their numbers",
          [(r, n) for r, n, _ in kept] == [(rel, 3), (rel, 4)], kept)
    check("and left in place", path.read_bytes() == before)

    code, out, _ = run("mark", "--root", str(tmp_path), "-c", _no_config(tmp_path),
                       "--remove")
    check("exit 0", code == 0, code)
    check("each one is printed as file:line", f"{rel}:3:" in out and f"{rel}:4:" in out, out)
    check("and the output does not call the tree clean",
          "no `echolot:mark` lines found" not in out, out)


# --- which activity is the launcher ------------------------------------------

def test_a_self_closing_activity_before_the_launcher_is_not_the_launcher(tmp_path):
    """`<activity … />` has no body, and the pattern read on to the next
    `</activity>` for one — taking the launcher's intent-filter with it, so
    `--apply` marked the settings screen's onCreate."""
    text = manifest('<activity android:name=".SettingsActivity" />', LAUNCHER)
    check("the launcher is the launcher", mark.launcher_activities(text) == [".MainActivity"],
          mark.launcher_activities(text))

    write(tmp_path, "app/src/main/AndroidManifest.xml", text)
    for name in ("SettingsActivity", "MainActivity"):
        write(tmp_path, f"app/src/main/kotlin/{name}.kt", (
            f"class {name} : ComponentActivity() {{\n"
            "    override fun onCreate(savedInstanceState: Bundle?) {\n"
            "        super.onCreate(savedInstanceState)\n"
            "    }\n"
            "}\n"))
    settings = (tmp_path / "app/src/main/kotlin/SettingsActivity.kt").read_bytes()
    code, out, _ = run("mark", "--root", str(tmp_path), "-c", _no_config(tmp_path),
                       "--apply")
    check("exit 0", code == 0, out)
    check("the launcher's onCreate is marked", "AGENTTMP_activity_oncreate" in
          (tmp_path / "app/src/main/kotlin/MainActivity.kt").read_text(encoding="utf-8"), out)
    check("the settings screen is not touched",
          (tmp_path / "app/src/main/kotlin/SettingsActivity.kt").read_bytes() == settings, out)


def test_aliases_of_one_activity_are_one_launcher(tmp_path):
    """An app that switches its icon has an alias per icon, all pointing at
    one activity. Counted once each, that was "several launcher activities",
    an ambiguity that stopped `--apply` and that `--module` could not settle."""
    text = manifest(
        LAUNCHER,
        f'<activity-alias android:name=".Classic" android:targetActivity=".MainActivity"'
        f' android:enabled="false">{FILTER}</activity-alias>',
        f'<activity-alias android:name=".Dark" android:enabled="false"\n'
        f'        android:targetActivity="com.example.app.MainActivity">{FILTER}</activity-alias>')
    check("one launcher", mark.launcher_activities(text) == [".MainActivity"],
          mark.launcher_activities(text))

    write(tmp_path, "app/src/main/AndroidManifest.xml", text)
    write(tmp_path, "app/src/main/kotlin/MainActivity.kt", (
        "class MainActivity : ComponentActivity() {\n"
        "    override fun onCreate(savedInstanceState: Bundle?) {\n"
        "        super.onCreate(savedInstanceState)\n"
        "    }\n"
        "}\n"))
    pl = mark.plan(tmp_path)
    check("no ambiguity", pl.ambiguity == [], pl.ambiguity)
    code, _, _ = run("mark", "--root", str(tmp_path), "-c", _no_config(tmp_path))
    check("and the command exits 0", code == 0, code)


def test_a_commented_out_launcher_is_not_one(tmp_path):
    text = manifest(f'<!-- <activity android:name=".OldMain">{FILTER}</activity> -->',
                    LAUNCHER)
    check("the build does not see it, and neither does mark",
          mark.launcher_activities(text) == [".MainActivity"], mark.launcher_activities(text))


def test_a_quoted_angle_bracket_does_not_end_the_tag():
    text = manifest(f'<activity android:label="a > b" android:name=".MainActivity">'
                    f'{FILTER}</activity>')
    check("still read", mark.launcher_activities(text) == [".MainActivity"],
          mark.launcher_activities(text))


def test_two_real_launchers_do_not_send_the_reader_to_module(tmp_path):
    """Two launchers in one manifest are two entry points of one module:
    `--module` chooses between modules and cannot settle it."""
    text = manifest(LAUNCHER, f'<activity android:name=".Tools">{FILTER}</activity>')
    check("both listed", mark.launcher_activities(text) == [".MainActivity", ".Tools"],
          mark.launcher_activities(text))
    write(tmp_path, "app/src/main/AndroidManifest.xml", text)
    pl = mark.plan(tmp_path)
    check("an ambiguity", len(pl.ambiguity) == 1, pl.ambiguity)
    check("without the flag that cannot help", "--module" not in pl.ambiguity[0], pl.ambiguity)


def test_the_base_class_is_named_for_java_as_for_kotlin(tmp_path):
    write(tmp_path, "app/src/main/AndroidManifest.xml", manifest(LAUNCHER))
    write(tmp_path, "app/src/main/java/MainActivity.java",
          "public class MainActivity extends BaseActivity implements Screen {\n}\n")
    notes = mark.plan(tmp_path).notes
    check("the note names where the override may be",
          any("inherits from BaseActivity" in n for n in notes), notes)
    check("a Kotlin constructor's parameter types are not the base",
          mark.base_class("class Main(val a: Int) : Base() {}", "Main") == "Base")
    check("and Kotlin's plain form still reads",
          mark.base_class("class Main : AppCompatActivity() {", "Main") == "AppCompatActivity")


# --- instrumentation.allowed ---------------------------------------------------

def test_allowed_roots_are_globs_segment_by_segment():
    """`scan` writes `feature/*/src/main`, the example config shows it, and
    `reflect` read it as a glob — while `mark` compared it as a prefix and
    refused every site in every `feature/*` module."""
    allowed = ["app/src/main", "feature/*/src/main"]
    cases = {
        "feature/login/src/main/kotlin/A.kt": True,
        "app/src/main/kotlin/A.kt": True,
        "feature/login/src/test/kotlin/A.kt": False,
        "feature/src/main/kotlin/A.kt": False,
        "feature/a/b/src/main/A.kt": False,      # one directory per `*`
        "core/ui/src/main/kotlin/A.kt": False,
        "app/src/mainline/A.kt": False,
    }
    for rel, inside in cases.items():
        check(f"{rel} is {'inside' if inside else 'outside'}",
              mark.under_allowed(rel, allowed) is inside)
        check(f"reflect reads {rel} the same way", facts._under_any(rel, allowed) is inside)
    check("no list, no limit", mark.under_allowed("anything/at/all.kt", []))
    check("trailing slashes are the same root",
          mark.under_allowed("app/src/main/A.kt", ["app/src/main/"]))


def test_plan_honours_a_glob(tmp_path):
    write(tmp_path, "app/src/main/AndroidManifest.xml", manifest(LAUNCHER, application=""))
    write(tmp_path, "feature/main/src/main/kotlin/MainActivity.kt", (
        "class MainActivity : ComponentActivity() {\n"
        "    override fun onCreate(savedInstanceState: Bundle?) {\n"
        "        super.onCreate(savedInstanceState)\n"
        "    }\n"
        "}\n"))
    pl = mark.plan(tmp_path, allowed=["feature/*/src/main"])
    oc = next(p for p in pl.proposals if p.kind == "activity_oncreate")
    check("a site under the glob is applicable", oc.applicable, oc.reason)
    pl = mark.plan(tmp_path, allowed=["app/src/main"])
    oc = next(p for p in pl.proposals if p.kind == "activity_oncreate")
    check("and one outside still shown, and refused", not oc.applicable, oc)
    check("with the note", "outside instrumentation.allowed" in oc.reason, oc.reason)


def test_a_frame_outside_allowed_is_shown_not_dropped(tmp_path):
    """`--from-anr` skipped such a frame without a row, where `plan` shows a
    site outside and refuses it — and the docs said the same of both."""
    body = "class Store {\n    fun flush() {\n        write()\n    }\n}\n"
    inside = "feature/cart/src/main/kotlin/Store.kt"
    outside = "core/data/src/main/kotlin/Store.kt"
    write(tmp_path, inside, body)
    write(tmp_path, outside, body.replace("Store", "Cache"))
    pl = mark.plan_from_anr(tmp_path, [("com.example.Store.flush", inside, 3),
                                       ("com.example.Cache.flush", outside, 3)],
                            allowed=["feature/*/src/main"])
    by = {p.file: p for p in pl.proposals}
    check("both frames have a row", set(by) == {inside, outside}, pl.proposals)
    check("the one under the glob is applicable", by[inside].applicable, by[inside].reason)
    check("the one outside is refused, and says why",
          not by[outside].applicable
          and "outside instrumentation.allowed" in by[outside].reason, by[outside])


def test_pools_honour_a_glob_and_say_outside_plainly(tmp_path):
    write(tmp_path, "feature/sync/src/main/kotlin/A.kt",
          "val a = Executors.newSingleThreadExecutor()\n")
    write(tmp_path, "core/io/src/main/kotlin/B.kt",
          "val b = Executors.newSingleThreadExecutor()\n")
    pl = mark.plan_pools(tmp_path, allowed=["feature/*/src/main"])
    by = {p.file: p.reason for p in pl.proposals}
    check("inside the glob, nothing to add", by["feature/sync/src/main/kotlin/A.kt"] == "", by)
    check("outside, said once and without a dangling separator",
          by["core/io/src/main/kotlin/B.kt"] == "outside instrumentation.allowed", by)


# --- --pools: which threads already have a name ---------------------------------

def _pools(root: Path, kotlin: str, java: str = "") -> list[tuple[int, str]]:
    write(root, "app/src/main/kotlin/P.kt", kotlin)
    if java:
        write(root, "app/src/main/java/J.java", java)
    return [(p.line, p.what.split(" — ")[0]) for p in mark.plan_pools(root).proposals]


def test_a_thread_given_a_name_is_not_a_finding(tmp_path):
    """`strip_noise` blanked `"io"` out of `Thread(r, "io")` before anything
    looked for a name, and a factory passed as a trailing lambda sat outside
    the parentheses that were read."""
    found = _pools(tmp_path, (
        'val a = Thread(r, "io")\n'
        'val b = Thread("sync")\n'
        'val c = Executors.newFixedThreadPool(2) { r -> Thread(r, "io-$n") }\n'
        "val d = Thread(r, THREAD_NAME)\n"
        'val e = object : Thread("worker") { override fun run() {} }\n'
        'val f = ThreadPoolExecutor(1, 1, 0L, SECONDS, queue) { r -> Thread(r, "x") }\n'),
        java='class J { Thread t = new Thread(r, "x" + n); }\n')
    check("none of them", found == [], found)


def test_a_thread_without_one_still_is(tmp_path):
    found = _pools(tmp_path, (
        "val a = Executors.newSingleThreadExecutor()\n"
        "val b = Thread(task)\n"
        'val c = Thread(Runnable { log("not a name") })\n'
        'val d = Thread(task /* "not a name either" */)\n'
        "val e = Executors.newFixedThreadPool(2) { r -> Thread(r) }\n"),
        java="class J { Thread t = new Thread(() -> work()); }\n")
    check("each one, once", found == [
        (1, "Thread"),                       # J.java sorts first
        (1, "Executors.newSingleThreadExecutor"),
        (2, "Thread"),
        (3, "Thread"),
        (4, "Thread"),
        # the factory's own `Thread(r)` is where the name goes, and the one row
        (5, "Thread"),
    ], found)
