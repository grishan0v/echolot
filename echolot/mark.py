"""`echolot mark` — where the first temporary markers go, and putting them there.

A project with no instrumentation gives the detectors nothing to name but
system slices and threads — `bindApplication`, `Compose:recompose`,
`arch_disk_io_0` — and `domains` has nothing to map them to. The agent then
reads the application to decide where the first `AGENTTMP_` markers belong;
in two hunts out of two that reading was half the window. This command does
that step, and it does it the only way that gives the same answer on any
project: it binds to the platform's vocabulary and never to the project's.

    manifest    the launcher Activity is the <intent-filter> with MAIN and
                LAUNCHER; the Application class is android:name on
                <application>. Structure, no names.
    lifecycle   `onCreate` — the method name is the SDK's, whatever the
                class is called.
    api         `setContent {`, `setContentView(`, `Room.databaseBuilder(`,
                `startKoin {`, `@HiltAndroidApp` — exact strings from
                someone else's library.
    call        the composables invoked directly inside `setContent { }`
                — one hop, resolved by an exact `fun Name(` search, kept
                only when the definition is in this project.
    jdk         `--pools`: `Executors.new*`, `ThreadPoolExecutor(`, a bare
                `Thread(` or Kotlin's `Thread { … }` — the places that hand
                the JDK's default factory a thread to name, so the report
                ends up saying
                `pool-7-thread-1`. A different question from the rest, and
                the only one that starts from the report; see `plan_pools`.

Nothing here matches `*ViewModel`, `*Repository`, `*Screen` or any other
convention. Every proposal carries its source, so a reader can see how firm
the ground is; what cannot be found is said as not found, never guessed;
the output is sorted and byte-for-byte the same for the same tree.

Applying is mechanical and reversible: each inserted line is a whole line of
its own ending in the tag `// echolot:mark`, put between two lines of the
project's and never into one, and `--remove` deletes exactly those lines.
Both halves of that sentence are load-bearing, so a block that cannot take a
line of its own is refused rather than approximated:

    a `return` in the body   the end would be skipped;
    a body written on one    the begin and end lines would cross, and the
    line                     body would end up inside the begin line's
                             comment — where `--remove` would then delete it;
    code after the `{`, or   the new line could only get in by splitting a
    before the `}`, on the   line of the project's, and `--remove` deletes
    brace's line             lines, it does not join them back.

All three are reported as "mark by hand" and shown with the reason. A comment
after the `{` is not code: the begin line goes in under it and the comment
stays where it was. Nothing else in the file is touched, its line endings
included, so `--remove` gives back the bytes `--apply` was given.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .domains import files_ending, files_named, gradle_module
from .domains import source_files as domains_source_files

DEFAULT_PREFIX = "AGENTTMP_"
TAG = "// echolot:mark"
# Exactly what `apply` writes, and nothing else. `remove` deletes whole lines,
# so a rule as loose as "the tag is somewhere in it" takes the line's real code
# along — which is how a one-line block used to be destroyed rather than merely
# mangled. A hand-written tag on a line of code is left alone, and `remove`
# returns it with its line number so the command can say where it is: kept
# without a word, it let "no `echolot:mark` lines found" stand over a tree
# that still had them.
_APPLIED_LINE = re.compile(
    r"^\s*android\.os\.Trace\.(?:begin|end)Section\s*\([^)]*\)\s*;?\s*"
    + re.escape(TAG) + r"\s*$")
CAP = 7   # proposals shown; the rest is a count. Five to seven is a skeleton, not a survey.

# --- the vocabulary -----------------------------------------------------------

# The opening tag of an <activity> or <activity-alias>, the body read
# separately — see `launcher_activities`. A quoted value is taken whole, so a
# `>` inside one does not end the tag, and `closed` is the slash of a tag that
# closes itself.
_ACTIVITY_OPEN = re.compile(
    r"<(?P<tag>activity(?:-alias)?)\b(?P<attrs>(?:[^>\"'/]|\"[^\"]*\"|'[^']*')*)"
    r"(?P<closed>/)?>")
_XML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_ANDROID_NAME = re.compile(r"android:name\s*=\s*\"([^\"]+)\"")
_TARGET_ACTIVITY = re.compile(r"android:targetActivity\s*=\s*\"([^\"]+)\"")
_ACTION_MAIN = re.compile(r"android\.intent\.action\.MAIN")
_CATEGORY_LAUNCHER = re.compile(r"android\.intent\.category\.LAUNCHER")
_APPLICATION_TAG = re.compile(r"<application\b([^>]*)>", re.S)
_MANIFEST_PACKAGE = re.compile(r"<manifest\b[^>]*\bpackage\s*=\s*\"([^\"]+)\"", re.S)
_NAMESPACE = re.compile(r"\b(?:namespace|applicationId)\s*(?:=|\s)\s*[\"']([^\"']+)[\"']")

# Kotlin `override fun onCreate(` / Java `protected void onCreate(`.
# An annotation on the same line (`@Override protected void onCreate(`) and
# `final` count: missing them, the launcher was said not to override onCreate.
_ON_CREATE = re.compile(
    r"^[ \t]*(?:@[\w.]+(?:\([^)]*\))?[ \t]+)*"
    r"(?:(?:override|open|final|public|protected|private)[ \t]+)*"
    r"(?:fun|void)[ \t]+onCreate[ \t]*\(", re.M)
_SET_CONTENT = re.compile(r"\bsetContent\s*(?:\([^)]*\)\s*)?\{")
_SET_CONTENT_VIEW = re.compile(r"\bsetContentView\s*\(")
_COMPOSABLE = re.compile(r"@Composable\b")
_ROOM_BUILDER = re.compile(r"\bRoom\s*\.\s*(?:databaseBuilder|inMemoryDatabaseBuilder)\s*\(")
_KOIN_START = re.compile(r"\bstartKoin\s*\{")
_HILT_APP = re.compile(r"@HiltAndroidApp\b")
# --- where anonymous threads are born -----------------------------------------
#
# The same rule as everything above: exact strings from somebody else's
# library, here the JDK's. A pool made through `Executors` or a bare `Thread`
# gets the default factory, and the default factory names its threads
# `pool-3-thread-1` and `Thread-12`. Those names reach the trace, every
# detector groups by them, and they say nothing.
#
# `HandlerThread(` is deliberately absent: it takes a name as its first
# argument, so it is already answered. Leaving it in counted fifteen sites on
# a real project where four were the real ones.
_EXECUTORS = re.compile(r"\bExecutors\s*\.\s*new([A-Za-z]+)\s*\(")
_POOL_CTOR = re.compile(r"\b(ThreadPoolExecutor|ScheduledThreadPoolExecutor|ForkJoinPool)\s*\(")
_BARE_THREAD = re.compile(r"(?<![A-Za-z0-9_])Thread\s*\(")
# Kotlin's `Thread { work() }`: the Runnable as a trailing lambda, and no
# parentheses to find. Its thread is `Thread-N` like any other; a return type,
# `fun worker(): Thread {`, is told apart by the colon before it.
_THREAD_LAMBDA = re.compile(r"(?<![A-Za-z0-9_.])Thread\s*\{")
# Already named, and the reason each is not a finding.
_NAMED_ALREADY = re.compile(
    r"\bThreadFactoryBuilder\b|\bsetNameFormat\b|"
    r"\bThread\.currentThread\(\)\s*\.\s*name\s*=|"
    r"\bnewThread\s*\(")
# Linux truncates a thread's `comm` to 15 characters, and the trace carries
# what is left: `pool-12-thread-` and `m.example.myapp`, the main thread of
# `com.example.myapp`, are both cut. A name longer than this is a name you
# will not read back.
COMM_MAX = 15

_CLASS_DECL = re.compile(r"\b(?:class|object)\s+([A-Za-z_][A-Za-z0-9_]*)")
_FUN_DECL = re.compile(r"\bfun\s+(?:<[^>]*>\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_RUNTIME_TRACING = re.compile(r"runtime[-.]tracing")
# The library runtime-tracing is built on. A project that has it has done the
# Perfetto SDK half of composition tracing already, and telling that project
# it has nothing is both wrong and the fastest way to be ignored.
_TRACING_PERFETTO = re.compile(r"tracing[-.]perfetto")
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?=[({])")
_KEYWORDS = {"if", "for", "while", "when", "try", "catch", "finally", "return", "else",
             "do", "run", "let", "also", "apply", "with", "repeat", "synchronized",
             "throw", "object", "fun", "class", "val", "var", "super", "this"}


def is_applied_line(line: str) -> bool:
    """A line `--apply` wrote: safe for `--remove` to delete whole."""
    return bool(_APPLIED_LINE.match(line))


@dataclass
class Proposal:
    kind: str                 # app_oncreate | activity_oncreate | set_content | set_content_view | compose_root | room_open | di_koin | anr_frame | pool_name | thread_name
    file: str                 # relative to root
    line: int                 # 1-based
    what: str                 # for a human
    marker: str               # AGENTTMP_…
    source: str               # manifest+lifecycle | api | call-from-setContent | anr | jdk
    module: str
    applicable: bool          # --apply can do it mechanically
    reason: str = ""          # why not, or a caveat
    # for --apply: 0-based char offsets into the file's text
    open_at: int | None = None
    close_at: int | None = None
    lambda_body: bool = False


@dataclass
class Plan:
    root: str
    module: str | None
    package: str | None
    proposals: list[Proposal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)       # facts worth saying, not sites
    ambiguity: list[str] = field(default_factory=list)   # what needs a human's choice
    hidden: int = 0                                      # proposals beyond CAP

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for p in d["proposals"]:
            for k in ("open_at", "close_at", "lambda_body"):
                p.pop(k, None)
        return d


# --- text helpers ---------------------------------------------------------------

def read_source(path: Path, strict: bool = False) -> str:
    """A source file's text as it is on disk, its line endings included.

    `read_text` turns `\\r\\n` into `\\n` on the way in and `write_text` writes
    `\\n` on the way out, so a CRLF file went through `--apply` and `--remove`
    and came back with every line ending changed — from a command whose whole
    promise is that the file comes back as it was. Decoded here with nothing
    translated, the offsets `plan` computes are offsets into the very text
    `apply` edits and writes back.

    `strict` is for the two that write: see `apply` for why a file that is
    not valid UTF-8 must not be read leniently there.
    """
    return path.read_bytes().decode("utf-8", errors="strict" if strict else "replace")


def strip_noise(text: str, strings: bool = True) -> str:
    """Strings and comments replaced by spaces, length and newlines kept.

    Brace matching and `return` detection run on this view, so a `}` inside
    a string literal or a `// return early` comment does not count.

    `strings=False` blanks the comments and leaves the strings: `--pools`
    has to see a string where the code has one — `Thread(r, "io")` is a
    thread with a name — and still not take a quote inside a comment for one.
    """
    out = list(text)
    i = 0
    while i < len(text):
        end, is_string = _noise_at(text, i)
        if end is None:
            i += 1
            continue
        if strings or not is_string:
            _blank(out, i, end)
        i = end
    return "".join(out)


def _noise_at(text: str, i: int) -> tuple[int | None, bool]:
    """Where the comment or string literal that starts at `i` ends, and
    whether it is a string. None when neither starts there.

    An unclosed comment or triple-quoted string runs to the end of the text;
    a quoted string that meets the end of its line ends there.
    """
    n = len(text)
    if text.startswith("//", i):
        j = text.find("\n", i)
        return (n if j < 0 else j), False
    if text.startswith("/*", i):
        j = text.find("*/", i + 2)
        return (n if j < 0 else j + 2), False
    if text.startswith('"""', i):
        j = text.find('"""', i + 3)
        return (n if j < 0 else j + 3), True
    if text[i] in "\"'":
        return _quoted_end(text, i), True
    return None, False


def _quoted_end(text: str, i: int) -> int:
    """Just past the quote that closes the one opened at `i`, a backslash
    escaping the character after it; or past the end of the line, for a
    string the line ends first."""
    quote, j = text[i], i + 1
    while j < len(text) and text[j] != quote and text[j] != "\n":
        j += 2 if text[j] == "\\" else 1
    return min(len(text), j + 1)


def _blank(out: list[str], start: int, end: int) -> None:
    """Spaces over [start, end), every newline kept where it was."""
    for k in range(start, end):
        if out[k] != "\n":
            out[k] = " "


def match_brace(clean: str, open_at: int) -> int | None:
    """Index of the `}` closing the `{` at open_at, on a noise-free text."""
    depth = 0
    for i in range(open_at, len(clean)):
        c = clean[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def one_line_body(text: str, open_at: int | None, close_at: int | None) -> bool:
    """Is this block's `{ … }` all on one source line?

    `apply` puts the begin line under the `{`'s line and the end line over
    the `}`'s, and on one line there is no room between the two. An earlier
    `apply` put them in anyway, and the second insert landed *before* the
    first: the end marker came out above the block, and the body was
    swallowed by the begin line's trailing `// echolot:mark`. `remove` then
    deleted that line whole and the body went with it, which is the one thing
    this module promises never to do.

    `setContent { AppRoot() }` is the shape a great deal of Compose is written
    in, so this is not a corner. Refused and said out loud, the way a `return`
    in the body already is.
    """
    if open_at is None or close_at is None:
        return False
    return "\n" not in text[open_at:close_at]


# What may share a line with a block's brace and still leave that line to
# itself: blanks, and comments that close on the same line — after the `{`
# a `/* … */` or a `// …`, before the `}` only the first. Anything else is
# the project's code.
_OPEN_TAIL = re.compile(r"[ \t]*(?:/\*(?:(?!\*/).)*\*/[ \t]*)*(?://.*)?\r?")
_CLOSE_HEAD = re.compile(r"[ \t]*(?:/\*(?:(?!\*/).)*\*/[ \t]*)*")


def brace_lines(text: str, open_at: int, close_at: int) -> tuple[int, int] | str:
    """Where whole lines go in around a block's body, or why they cannot.

    `(begin_at, end_at)`: the start of the line after the `{`'s, and the
    start of the `}`'s own line. A line put in at either sits between two of
    the project's lines, and deleting it gives back exactly what was there.

    That holds only while the `{` ends its line and the `}` begins its own,
    blanks and comments aside. `apply` used to put the begin line right after
    the `{` whatever followed it: `setContent { AppTheme {` became a begin
    line with `AppTheme {` trailing behind its `// echolot:mark`, commented
    out, and the file stopped compiling — while `--remove`, which deletes only
    lines of the exact applied shape, could not take that line back out. A
    comment after the `{` went the same way and left a begin with no end once
    `--remove` had run. Code before the `}` would have put the end line above
    it, inside whatever block that code closes. `--remove` deletes lines and
    joins none back together, so a block like that is refused, and the reason
    names the line to move.
    """
    eol = text.find("\n", open_at)
    if eol < 0 or eol >= close_at:
        return "the whole body is on one line — split the block, or mark by hand"
    if not _OPEN_TAIL.fullmatch(text, open_at + 1, eol):
        return ("code follows the `{` on its line — move it to a line of its "
                "own, or mark by hand")
    bol = text.rfind("\n", 0, close_at) + 1
    if not _CLOSE_HEAD.fullmatch(text, bol, close_at):
        return ("code comes before the `}` on its line — move it to a line of "
                "its own, or mark by hand")
    return eol + 1, bol


def _why_not(open_at: int | None, has_return: bool, flat: bool,
             close_at: int | None = 0, text: str | None = None) -> str:
    """Why a block cannot take a begin/end pair mechanically. Empty when it can.

    Every refusal has to carry its reason. A row printed with `·` and nothing
    after it reads as the tool declining without saying why, and there is no
    way for a reader to tell that from a bug — which is what the unclosed
    brace below produced: `find_lambda` finds the `{` and `match_brace`
    returns None, so the proposal was not applicable and the reason was the
    empty string.

    With the file's `text`, a block whose braces share their lines with code
    is refused here too — see `brace_lines` — so the plan says so before
    `--apply` has to.
    """
    if open_at is None:
        return "no block body found"
    if close_at is None:
        return "the block's closing brace was not found — the file may not parse"
    if has_return:
        return "has a return in its body — mark by hand"
    if flat:
        return "the whole body is on one line — split the block, or mark by hand"
    if text is not None:
        room = brace_lines(text, open_at, close_at)
        if isinstance(room, str):
            return room
    return ""


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# --- discovery -----------------------------------------------------------------

def source_files(root: Path) -> list[Path]:
    """This module's half of the walk: only what sits under a `src/`.

    The walk itself is `domains.source_files`, which prunes as it goes
    instead of reading the whole tree and discarding afterwards.
    """
    return domains_source_files(root, under_src=True)


def manifests(root: Path) -> list[Path]:
    """Every src/main manifest in the project, from the pruned walk.

    `rglob` found them inside a git worktree parked under `.claude/` too, so
    the app module appeared twice and `plan` asked for `--module` to tell two
    copies of the same manifest apart.
    """
    # Relative to the root: a checkout inside a directory named `main` let
    # every `src/debug` manifest through.
    return [p for p in files_named(root, "AndroidManifest.xml")
            if "main" in p.relative_to(root).parts]


def launcher_activities(text: str) -> list[str]:
    """Class names of activities whose intent-filter has MAIN and LAUNCHER.

    An element's body runs to its own closing tag and never past the next
    activity's opening one, and a tag that closes itself has no body at all.
    One pattern used to do both jobs, and for `<activity android:name=
    ".SettingsActivity" />` it read on to the next `</activity>` — taking the
    launcher's intent-filter along, so the settings screen was named the
    entry point and `--apply` marked its onCreate. Commented-out elements are
    dropped first: the build does not see them either.

    An `<activity-alias>` names its class by `targetActivity`, and an app that
    switches its icon has several aliases for one activity. Each class is
    listed once, by its simple name — the one every search below uses — or
    the same activity counted twice was "several launcher activities", an
    ambiguity that stopped `--apply` and that no flag could settle.
    """
    text = _XML_COMMENT.sub("", text)
    opens = list(_ACTIVITY_OPEN.finditer(text))
    out: list[str] = []
    for i, m in enumerate(opens):
        if m.group("closed"):
            continue
        stop = opens[i + 1].start() if i + 1 < len(opens) else len(text)
        close = re.compile(r"</" + m.group("tag") + r"\s*>").search(text, m.end(), stop)
        body = text[m.end():close.start() if close else stop]
        if not (_ACTION_MAIN.search(body) and _CATEGORY_LAUNCHER.search(body)):
            continue
        attrs = m.group("attrs")
        chosen = _TARGET_ACTIVITY.search(attrs) or _ANDROID_NAME.search(attrs)
        if chosen and simple_name(chosen.group(1)) not in {simple_name(n) for n in out}:
            out.append(chosen.group(1))
    return out


def application_class(text: str) -> str | None:
    m = _APPLICATION_TAG.search(text)
    if not m:
        return None
    n = _ANDROID_NAME.search(m.group(1))
    return n.group(1) if n else None


def module_dir_of(manifest: Path) -> Path:
    # <module>/src/main/AndroidManifest.xml
    p = manifest.parent
    while p.name != "src" and p.parent != p:
        p = p.parent
    return p.parent if p.name == "src" else manifest.parent


def module_package(module_dir: Path, manifest_text: str) -> str | None:
    m = _MANIFEST_PACKAGE.search(manifest_text)
    if m:
        return m.group(1)
    for name in ("build.gradle.kts", "build.gradle"):
        f = module_dir / name
        if f.exists():
            t = f.read_text(encoding="utf-8", errors="replace")
            m = _NAMESPACE.search(t)
            if m:
                return m.group(1)
    return None


def module_ids(module_dir: Path, manifest_text: str) -> set[str]:
    """Every id a module is known by: the manifest's package, its namespace
    and its applicationId — what `project.package` can be one of, with a
    suffix after it."""
    ids = set(_MANIFEST_PACKAGE.findall(manifest_text))
    for name in ("build.gradle.kts", "build.gradle"):
        f = module_dir / name
        if f.exists():
            ids.update(_NAMESPACE.findall(f.read_text(encoding="utf-8", errors="replace")))
    return ids


def _installs_as(package: str, ids: set[str], exact: bool) -> bool:
    """Whether a module known by `ids` installs as `package`.

    `project.package` is the package as installed — for a benchmark build
    often `com.example.app.benchmark` — and a module's namespace equalled it
    only without a suffix. A glob, what `project.process` holds, is matched as
    one.
    """
    if any(c in package for c in "*?["):
        return any(fnmatch.fnmatchcase(i, package) for i in ids)
    if exact:
        return package in ids
    return any(package.startswith(i + ".") for i in ids)


def simple_name(class_ref: str) -> str:
    return class_ref.rsplit(".", 1)[-1]


def read_sources(files: list[Path]) -> dict[Path, str]:
    """Every source read once, for the searches below to share.

    `plan` looks for the Application class, then the launcher Activity, then
    a composable per name `setContent` calls, then Room and Koin and Hilt —
    and each search used to open every file in the project again. On a
    checkout of any size that is the whole tree read six or seven times over
    for four answers, and the searches are what `mark` spends its time on.

    Read by `read_source`, so the offsets taken from these texts are good
    for `apply` as they are.
    """
    out: dict[Path, str] = {}
    for p in files:
        try:
            out[p] = read_source(p)
        except OSError:
            continue
    return out


def find_class_file(root: Path, module_dir: Path | None, name: str,
                    sources: dict[Path, str]) -> Path | None:
    """The source file declaring `class <name>`, the manifest's module first."""
    pat = re.compile(r"\b(?:class|object)\s+" + re.escape(name) + r"\b")
    hits = [p for p, text in sources.items() if pat.search(text)]
    if not hits:
        return None
    if module_dir is not None:
        inside = [p for p in hits if module_dir in p.parents]
        if inside:
            return inside[0]
    return hits[0]


def base_class(text: str, name: str) -> str | None:
    """The class `name` inherits from, as its declaration writes it.

    Kotlin's `class Main : Base()` and Java's `class Main extends Base` both;
    only the first was read, so a Java launcher that does not override
    onCreate got the note without the one name that says where to look. A
    Kotlin primary constructor is stepped over whole, so the type of a
    parameter in it is not taken for the base.
    """
    m = re.search(r"\bclass\s+" + re.escape(name)
                  + r"\b(?:\([^)]*\)|[^{(])*?(?::|\bextends\b)\s*([A-Za-z_][\w.]*)", text)
    return m.group(1) if m else None


_RETURN = re.compile(r"\breturn\b(?:@(\w+))?")
_JAVA_NESTED = re.compile(r"->\s*\{|\bnew\s+[\w.<>]+\s*\([^()]*\)\s*\{")


def leaves(body: str, name: str, java: bool = False) -> bool:
    """Whether a `return` in this (noise-stripped) body leaves the function `name`.

    Kotlin: a bare `return`, which in an inline lambda leaves the function
    too, and `return@name`; any other label leaves only its lambda —
    `return@setOnClickListener` skipped no end line, and refused a whole
    onCreate. Java: a `return` inside a lambda body or an anonymous class
    leaves only that, so those bodies are blanked out first.
    """
    if java:
        out = list(body)
        for m in _JAVA_NESTED.finditer(body):
            close = match_brace(body, m.end() - 1)
            if close is not None:
                _blank(out, m.end(), close)
        return re.search(r"\breturn\b", "".join(out)) is not None
    return any(m.group(1) in (None, name) for m in _RETURN.finditer(body))


def class_body(clean: str, name: str) -> tuple[int, int] | None:
    """The `{` and `}` of class `name`'s body, in text with the noise blanked.

    Past a primary constructor and the supertypes, to the first `{` outside
    parentheses. None when the class is not declared here or has no body.
    """
    m = re.search(r"\b(?:class|object)\s+" + re.escape(name) + r"\b", clean)
    if not m:
        return None
    depth = 0
    for i in range(m.end(), len(clean)):
        if clean[i] == "(":
            depth += 1
        elif clean[i] == ")":
            depth -= 1
        elif clean[i] == "{" and depth == 0:
            k = match_brace(clean, i)
            return (i, k) if k is not None else None
    return None


def find_on_create(text: str, cls: str | None = None
                   ) -> tuple[int, int | None, int | None, bool] | None:
    """(line, open_at, close_at, has_return) of `cls`'s own onCreate override.

    The one directly in that class's body, one level deep. The first in the
    file was taken whoever it belonged to: a `RoomDatabase.Callback`'s
    `onCreate(db)` in a property above `App.onCreate`, or the Application's
    where the Activity shares its file, got the marker. And it is looked for
    in the code alone: an old `onCreate` kept in a comment made the next `{`
    in code the block, inside another function. Without a class, or with one
    this file does not declare, the first in the file.
    """
    clean = strip_noise(text)
    body = class_body(clean, cls) if cls else None
    lo, hi = (body[0] + 1, body[1]) if body else (0, len(clean))
    for m in _ON_CREATE.finditer(clean, lo, hi):
        before = clean[lo:m.start()]
        if body is None or before.count("{") == before.count("}"):
            break
    else:
        return None
    # the `{` after the signature's closing paren, allowing an annotation-free
    # single-line signature and a multi-line parameter list
    depth = 0
    i = m.end() - 1   # at "("
    while i < len(clean):
        if clean[i] == "(":
            depth += 1
        elif clean[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    j = clean.find("{", i)
    # `override fun onCreate(...) = …` is an expression body: no block to
    # bracket. Between `)` and `{` a block body has at most a return type.
    if j < 0 or "=" in clean[i:j] or ";" in clean[i:j]:
        return (line_of(text, m.start()), None, None, False)
    k = match_brace(clean, j)
    if k is None:
        return (line_of(text, m.start()), None, None, False)
    java = "void" in m.group(0)
    has_return = leaves(clean[j + 1:k], "onCreate", java)
    return (line_of(text, m.start()), j, k, has_return)


def find_lambda(text: str, rx: re.Pattern) -> tuple[int, int | None, int | None] | None:
    """(line, open_at, close_at) of the first `name { … }` matched by rx.

    In the code, not in a comment about it: a KDoc that said "Compose starts
    in setContent { } below" put the pair on the class body.
    """
    clean = strip_noise(text)
    m = rx.search(clean)
    if not m:
        return None
    j = clean.find("{", m.start())
    if j < 0:
        return (line_of(text, m.start()), None, None)
    k = match_brace(clean, j)
    return (line_of(text, m.start()), j, k)


def calls_inside(clean_body: str) -> list[str]:
    """Identifiers called inside a lambda body, in order of first appearance.

    `setContent { AppTheme { Surface { AppNavHost() } } }` → [AppTheme,
    Surface, AppNavHost]. Structural: an identifier followed by `(` or `{`,
    keywords dropped, no case rule. Which of these are this project's
    composables is settled afterwards by finding `@Composable fun Name(` in
    the sources — the library ones drop out there.
    """
    out: list[str] = []
    i = 0
    n = len(clean_body)
    while i < n:
        c = clean_body[i]
        if c.isalpha() or c == "_":
            m = _CALL.match(clean_body, i)
            if m:
                name = m.group(1)
                if name not in _KEYWORDS and name not in out:
                    out.append(name)
                i = m.end()
                continue
            j = i
            while j < n and (clean_body[j].isalnum() or clean_body[j] == "_"):
                j += 1
            i = max(j, i + 1)
            continue
        i += 1
    return out


def find_composable_decl(name: str,
                         sources: dict[Path, str]) -> tuple[Path, int] | None:
    """`@Composable fun <name>(` in this project's sources — the annotation is
    the platform's word for it, so a project function that merely shares a
    name with a library call is not mistaken for a screen."""
    pat = re.compile(r"@Composable\b[^{;]*?\bfun\s+(?:<[^>]*>\s*)?" + re.escape(name) + r"\s*\(", re.S)
    for p, text in sources.items():
        m = pat.search(text)
        if m:
            return p, line_of(text, m.end() - 1)
    return None


def under_allowed(rel: str, allowed: list[str]) -> bool:
    """Whether a path sits under one of `instrumentation.allowed`, globs included.

    An empty list allows everything. Otherwise each root is compared segment
    by segment, every segment a glob over the path's segment at the same
    depth, and the path may go deeper: `feature/*/src/main` covers
    `feature/login/src/main/kotlin/A.kt` and not `feature/login/src/test/…`.

    That is the form `scan` writes into the config and the example shows,
    and this used to compare it as a plain prefix — so every site in every
    `feature/*` module was refused as outside. `reflect` reads the same list
    through this function, so the command that writes the markers and the
    report that audits where they went cannot disagree about it.
    """
    if not allowed:
        return True
    parts = rel.split("/")
    for root in allowed:
        segs = [s for s in str(root).strip("/").split("/") if s]
        if segs and len(segs) <= len(parts) and all(
                fnmatch.fnmatchcase(p, s) for p, s in zip(parts, segs, strict=False)):
            return True
    return False


_OUTSIDE = "outside instrumentation.allowed — mark the nearest allowed caller instead"


def _refuse_outside(p: Proposal, allowed: list[str], why: str = _OUTSIDE) -> None:
    """Not applicable, and why, when the site is outside `allowed`.

    Shown rather than dropped: the joint is where it is, and a reader who
    does not see the row cannot know there was one. The reason is added to
    any the site already had, never in place of it.
    """
    if under_allowed(p.file, allowed):
        return
    p.applicable = False
    p.reason = "; ".join(r for r in (p.reason, why) if r)


def _build_files(root: Path, mdir: Path) -> tuple[list[Path], list[Path]]:
    """(the app module's build scripts, every other one in the project)."""
    mine = [mdir / n for n in ("build.gradle.kts", "build.gradle") if (mdir / n).exists()]
    others = [p for p in files_named(root, "build.gradle.kts") + files_named(root, "build.gradle")
              if p not in mine]
    return mine, others


def _seen_in(paths: list[Path], pattern) -> Path | None:
    for p in paths:
        try:
            if pattern.search(p.read_text(encoding="utf-8", errors="replace")):
                return p
        except OSError:
            continue
    return None


def _tracing_note(root: Path, mdir: Path) -> str:
    """What composition tracing needs, against what the project already has.

    The check used to read the app module's two build scripts and nothing
    else, so it said "not among the dependencies" about a project that
    declares the library in `gradle/libs.versions.toml` and applies it from a
    convention plugin — and about one that has `androidx.tracing:tracing-perfetto`
    in the catalog, which is what runtime-tracing is built on. Being told to
    start from zero by a tool that did not look is worse than not being told.

    Empty string when there is nothing to say.
    """
    mine, others = _build_files(root, mdir)
    catalogs = files_ending(root, ".versions.toml")
    if _seen_in(mine, _RUNTIME_TRACING):
        return ""
    elsewhere = _seen_in(others, _RUNTIME_TRACING)
    if elsewhere:
        return (f"androidx.compose.runtime:runtime-tracing is in "
                f"{_rel(elsewhere, root)} but not in the app module's build script — "
                f"check that :{_rel(mdir, root)} ends up with it, or composable names "
                f"will not be in the trace")
    if _seen_in(catalogs, _RUNTIME_TRACING):
        return ("androidx.compose.runtime:runtime-tracing is in the version catalog "
                "and not applied in the app module — one line in its dependencies "
                "and composable names appear in the trace with no markers at all")
    perfetto = _seen_in(mine + others + catalogs, _TRACING_PERFETTO)
    where = f" ({_rel(perfetto, root)})" if perfetto else ""
    if perfetto:
        return (f"androidx.tracing:tracing-perfetto is already here{where}, and "
                f"androidx.compose.runtime:runtime-tracing is the artifact on top of "
                f"it that names composables — with it they appear in the trace with "
                f"no markers at all; the one line left to add for a Compose app")
    return ("androidx.compose.runtime:runtime-tracing is not among the app module's "
            "dependencies, the version catalog, or any build script here — with it, "
            "composable names appear in the trace with no markers at all; the first "
            "thing to add for a Compose app")


def _uses_compose(sources: dict[Path, str], mdir: Path,
                  proposals: list[Proposal]) -> bool:
    """Whether the app module uses Compose, by the strings `plan` knows it by.

    runtime-tracing names composables, and an app without any has nothing for
    it to name. The note went to every app all the same, a Views-only one
    included, and told it the Compose library was the first thing to add — a
    line that is on every project's output is a line people learn to skip.

    The launcher's `setContent {` settles it, and `plan` has already looked
    for that one. Past it, any source under the app module that calls
    `setContent {` or declares a `@Composable`: a Compose screen behind a
    launcher written with Views, a `ComposeView` given its content.
    """
    if any(p.kind == "set_content" for p in proposals):
        return True
    return any(mdir in p.parents and (_SET_CONTENT.search(t) or _COMPOSABLE.search(t))
               for p, t in sources.items())


# --- the plan ------------------------------------------------------------------

def plan(root: Path, package: str | None = None, allowed: list[str] | None = None,
         prefix: str = DEFAULT_PREFIX, module: str | None = None) -> Plan:
    root = root.resolve()
    allowed = list(allowed or [])
    # Read once, searched many times. See `read_sources`.
    sources = read_sources(source_files(root))
    out = Plan(root=str(root), module=None, package=package)

    # 1. the app module: the manifest with a launcher activity
    candidates, found = _app_candidates(root, package, module)
    if not candidates and found:
        # A mistyped --module: the launchers are there.
        out.ambiguity.append(
            f"--module {module} matches none of: "
            + ", ".join(f"{gradle_module(c[0], root)} ({c[4] or 'package unknown'})" for c in found))
        return out
    if not candidates:
        out.notes.append("no launcher Activity in any AndroidManifest.xml under src/main — "
                         "this tree has no app entry point to mark (a library, or the app "
                         "module lives elsewhere: pass --root)")
        return out
    if len(candidates) > 1:
        # project.package can settle it only when the modules are known by
        # different ids; two copies of one module are not.
        distinct = len({frozenset(c[5]) for c in candidates}) == len(candidates)
        out.ambiguity.append(
            "several modules declare a launcher Activity: "
            + ", ".join(f"{gradle_module(c[0], root)} ({c[4] or 'package unknown'})" for c in candidates)
            + (" — pass --module, or set project.package so one matches" if distinct
               else " — pass --module"))
        return out
    mf, mdir, mtext, launchers, pkg, _ = candidates[0]
    out.module = gradle_module(mf, root)
    out.package = out.package or pkg
    if len(launchers) > 1:
        # Two different classes, aliases already folded into their target.
        # `--module` chooses between modules, not between two entry points of
        # one, so it is not offered as the way out.
        out.ambiguity.append(
            f"{_rel(mf, root)} declares {len(launchers)} launcher activities: "
            + ", ".join(launchers) + " — the proposals below are for the first, and "
            "--apply will not choose between two entry points: mark by hand")

    planner = _Planner(root, sources, prefix, allowed, out)
    planner.application(mdir, mtext)
    planner.activity(mdir, launchers[0])
    planner.apis()

    # 5. what would give names for free — to an app with composables to name
    if _uses_compose(sources, mdir, out.proposals):
        note = _tracing_note(root, mdir)
        if note:
            out.notes.append(note)

    # deterministic order: by kind rank, then path, then line; then the cap
    rank = {k: i for i, k in enumerate(("app_oncreate", "activity_oncreate", "set_content",
                                        "set_content_view", "compose_root", "room_open", "di_koin"))}
    out.proposals.sort(key=lambda p: (rank.get(p.kind, 99), p.file, p.line))
    if len(out.proposals) > CAP:
        out.hidden = len(out.proposals) - CAP
        out.proposals = out.proposals[:CAP]
    return out


def _app_candidates(root: Path, package: str | None, module: str | None
                    ) -> tuple[list[tuple], list[tuple]]:
    """The modules whose manifest has a launcher activity, narrowed by
    `--module`, and by the package when that leaves exactly one; and every
    launcher module before `--module`, for saying what it matched none of."""
    found = []
    for mf in manifests(root):
        text = mf.read_text(encoding="utf-8", errors="replace")
        launchers = launcher_activities(text)
        if not launchers:
            continue
        mdir = module_dir_of(mf)
        found.append((mf, mdir, text, launchers, module_package(mdir, text),
                      module_ids(mdir, text)))
    candidates = found
    if module:
        candidates = [c for c in candidates
                      if gradle_module(c[0], root) == module or _rel(c[1], root) == module.strip(":").replace(":", "/")]
    if len(candidates) > 1 and package:
        # Exact first: an installed id that is a module's own beats one that
        # merely starts with it.
        for exact in (True, False):
            narrowed = [c for c in candidates if _installs_as(package, c[5], exact)]
            if len(narrowed) == 1:
                candidates = narrowed
                break
    return candidates, found


@dataclass
class _Planner:
    """What steps 2 to 4 of `plan` share: the tree, its sources, and the plan
    they add to."""

    root: Path
    sources: dict[Path, str]
    prefix: str
    allowed: list[str]
    out: Plan

    def add(self, p: Proposal) -> None:
        _refuse_outside(p, self.allowed)
        self.out.proposals.append(p)

    def application(self, mdir: Path, mtext: str) -> None:
        """2. Application.onCreate"""
        root, out = self.root, self.out
        app_cls = application_class(mtext)
        if not app_cls:
            out.notes.append("no custom Application class in the manifest — what runs at "
                             "bindApplication is the ContentProviders and library initializers; "
                             "see app_init")
            return
        f = find_class_file(root, mdir, simple_name(app_cls), self.sources)
        if f is None:
            out.notes.append(f"Application class {app_cls} is declared in the manifest but no "
                             f"source declares it under src/ (generated, or in a dependency)")
            return
        t = self.sources[f]
        oc = find_on_create(t, simple_name(app_cls))
        if oc is None:
            out.notes.append(f"{_rel(f, root)}: {simple_name(app_cls)} does not override "
                             f"onCreate — what runs at bindApplication is its constructor, the "
                             f"ContentProviders and library initializers; see app_init")
            return
        line, o, c, ret = oc
        why = _why_not(o, ret, one_line_body(t, o, c), c, t)
        self.add(Proposal("app_oncreate", _rel(f, root), line,
                          f"{simple_name(app_cls)}.onCreate — what runs inside bindApplication",
                          self.prefix + "app_oncreate", "manifest+lifecycle",
                          gradle_module(f, root),
                          applicable=not why, reason=why,
                          open_at=o, close_at=c))

    def activity(self, mdir: Path, act: str) -> None:
        """3. launcher Activity: onCreate, setContent / setContentView"""
        root = self.root
        f = find_class_file(root, mdir, simple_name(act), self.sources)
        if f is None:
            self.out.notes.append(f"launcher Activity {act} is declared in the manifest but no "
                                  f"source declares it under src/ (generated, or in a dependency)")
            return
        t = self.sources[f]
        oc = find_on_create(t, simple_name(act))
        if oc is None:
            base = base_class(t, simple_name(act))
            self.out.notes.append(
                f"{_rel(f, root)}: {simple_name(act)} does not override onCreate"
                + (f" — it inherits from {base}; the override, if any, is there" if base else ""))
        else:
            line, o, c, ret = oc
            why = _why_not(o, ret, one_line_body(t, o, c), c, t)
            self.add(Proposal("activity_oncreate", _rel(f, root), line,
                              f"{simple_name(act)}.onCreate — the launcher Activity, what runs inside activityStart",
                              self.prefix + "activity_oncreate", "manifest+lifecycle",
                              gradle_module(f, root),
                              applicable=not why, reason=why,
                              open_at=o, close_at=c))
        sc = find_lambda(t, _SET_CONTENT)
        if sc:
            line, o, c = sc
            why = _why_not(o, False, one_line_body(t, o, c), c, t)
            self.add(Proposal("set_content", _rel(f, root), line,
                              "setContent { } — the root of the Compose tree; recomposition re-enters it",
                              self.prefix + "set_content", "api", gradle_module(f, root),
                              applicable=not why, reason=why,
                              open_at=o, close_at=c, lambda_body=True))
            if o is not None and c is not None:
                self.compose_roots(t, o, c)
            return
        m = _SET_CONTENT_VIEW.search(strip_noise(t))
        if m:
            self.add(Proposal("set_content_view", _rel(f, root), line_of(t, m.start()),
                              "setContentView(…) — the View hierarchy is inflated here",
                              self.prefix + "set_content_view", "api", gradle_module(f, root),
                              applicable=False, reason="a call, not a block — mark the "
                              "surrounding onCreate instead (proposed above)"))

    def compose_roots(self, t: str, o: int, c: int) -> None:
        """One hop: what setContent calls, when it is this project's code."""
        found = 0
        for name in calls_inside(strip_noise(t)[o + 1:c]):
            hit = find_composable_decl(name, self.sources)
            if hit is None:
                continue
            hf, hl = hit
            self.add(Proposal("compose_root", _rel(hf, self.root), hl,
                              f"@Composable {name}() — called from setContent, defined here",
                              self.prefix + "compose_" + name, "call-from-setContent",
                              gradle_module(hf, self.root), applicable=False,
                              reason="a composable: wrap its call site by hand, or use "
                                     "androidx.compose.runtime:runtime-tracing (see notes)"))
            found += 1
            if found >= 3:
                break

    def apis(self) -> None:
        """4. Room, Koin, Hilt — API strings anywhere in the sources"""
        for p, t in self.sources.items():
            rel = _rel(p, self.root)
            # The code only: a commented-out call is not where anything opens.
            code = strip_noise(t)
            for m in _ROOM_BUILDER.finditer(code):
                self.add(Proposal("room_open", rel, line_of(t, m.start()),
                                  "Room.databaseBuilder — the database is opened here",
                                  self.prefix + "room_open", "api", gradle_module(p, self.root),
                                  applicable=False,
                                  reason="a builder chain — wrap the enclosing function by hand"))
            for m in _KOIN_START.finditer(code):
                self.add(Proposal("di_koin", rel, line_of(t, m.start()),
                                  "startKoin { } — the DI graph is built here",
                                  self.prefix + "di_koin", "api", gradle_module(p, self.root),
                                  applicable=False, reason="mark the enclosing function by hand"))
            if _HILT_APP.search(code) and not any("Hilt" in n for n in self.out.notes):
                self.out.notes.append(f"{rel}: @HiltAndroidApp — the graph is generated; its cost sits "
                                      f"inside Application.onCreate (super.onCreate), nothing separate to mark")


# --- markers from a stack ---------------------------------------------------
#
# The vocabulary above proposes where instrumentation *usually* belongs on a
# project that has none: the launcher Activity, the Application class, one hop
# from setContent. That is a good guess and it is a guess.
#
# A stack from a freeze is not. It names the methods that were on the thread at
# the moment the system gave up, with the file and the line the compiler wrote
# into each frame. Marking those is marking what was measured to be there.

_KOTLIN_FUN = re.compile(
    r"^[ \t]*(?:@[\w.]+(?:\([^)]*\))?[ \t]*)*"
    r"(?:(?:public|private|internal|protected|suspend|inline|override|open|"
    r"final|abstract|tailrec|operator|infix|external|actual|expect)[ \t]+)*"
    r"fun[ \t]+(?:<[^>]*>[ \t]*)?(?:[\w.<>?]+\.)?(?P<name>[\w`]+)[ \t]*\("
)
_JAVA_DECL = re.compile(
    r"^[ \t]*(?:@\w+(?:\([^)]*\))?[ \t]*)*"
    # None of these is required. A method with no modifier is package-private,
    # which is how Java spells "only this package calls it", and requiring one
    # left `void flush() {` in no function at all.
    r"(?:(?:public|private|protected|static|final|abstract|synchronized|"
    r"native|default|strictfp)[ \t]+)*"
    # With no modifier in front, a statement has the same shape as a
    # declaration — `return fetch(`, `new Runnable() {`, `else if (` — and a
    # declaration never opens with one of these words.
    r"(?!(?:return|new|else|throw|yield|assert|case)\b)"
    r"(?:<[^>]*>[ \t]*)?[\w.<>\[\]?]+[ \t]+(?P<name>\w+)[ \t]*\("
)


def _body_of(clean: str, paren_at: int) -> tuple[int | None, int | None]:
    """The `{ … }` of a declaration whose parameter list opens at `paren_at`.

    The parameters are matched first because they may run over several lines,
    and the brace that follows them is the body's. An `=` or a `;` in between
    means there is no body to bracket: an expression-bodied function, or a
    declaration without an implementation.
    """
    depth, i = 0, paren_at
    while i < len(clean):
        if clean[i] == "(":
            depth += 1
        elif clean[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    j = clean.find("{", i)
    # A `fun` before the brace is the next declaration: an `abstract fun` or
    # an interface's ends with neither `{` nor `;`, and took the next one's body.
    if j < 0 or "=" in clean[i:j] or ";" in clean[i:j] or re.search(r"\bfun\b", clean[i:j]):
        return (None, None)
    k = match_brace(clean, j)
    return (j, k) if k is not None else (None, None)


def enclosing_block(text: str, suffix: str, line: int):
    """The innermost declaration whose body holds this 1-based line.

    Returns `(name, decl_line, open_at, close_at, has_return)`, or None when
    the line sits in no block this can see — a property initialiser, a field, a
    file whose shape these two patterns do not cover.

    Innermost rather than first: a frame often points inside a lambda, and the
    function around that lambda is the one worth bracketing, while an outer
    function containing both would put the marker around far too much.
    """
    clean = strip_noise(text)
    # Split on `\n` alone, as `line_of` counts and as a compiler does:
    # `splitlines` also breaks at a form feed, and every declaration after
    # one came out a line late.
    lines = re.findall(r"[^\n]*\n|[^\n]+$", text)
    if not 1 <= line <= len(lines):
        return None
    pattern = _KOTLIN_FUN if suffix == ".kt" else _JAVA_DECL

    # By line rather than by offset. A frame can point at the signature line
    # itself, whose offset is before the `{` — `fun brief() { write() }` would
    # then be found to contain nothing, including itself.
    best = None
    offset = 0
    for number, raw in enumerate(lines, 1):
        found = pattern.match(raw)
        if found:
            # The parameter list's own `(`, where the match ends: the line's
            # first one can be an annotation's.
            paren = offset + found.end() - 1
            open_at, close_at = _body_of(clean, paren)
            if open_at is not None and number <= line <= line_of(text, close_at):
                # On a tie the later declaration: the earlier one is a
                # declaration that found nobody's body but this one's.
                if best is None or open_at >= best[2]:
                    name = found.group("name").strip("`")
                    best = (name, number, open_at, close_at,
                            leaves(clean[open_at + 1:close_at], name, suffix != ".kt"))
        offset += len(raw)
    return best


# The two ways into a lambda that Kotlin compiled into a class of its own. A
# method a person named is hardly ever one of them on a class numbered last:
# an anonymous object's `onClick` is declared in the source, and is the answer
# as it stands.
_LAMBDA_ENTRY = ("invoke", "invokeSuspend")


def _as_written(member: str) -> str:
    """A method's name with what the compiler added to it taken off.

    javac lifts a lambda into `lambda$<function>$N`. Kotlin compiling with
    invokedynamic writes `<function>$lambda$N` (`$lambda-N` before 1.8), gives
    a function with default arguments a `<function>$default` beside it and an
    open suspend one a `$suspendImpl`, and puts a `-hash` on a name that takes
    an inline class. Neither a `$` nor a `-` belongs in a name a person gives
    — Java keeps the first for generated code, Kotlin allows neither — so
    what comes before the first of them is that name.
    """
    if member.startswith("lambda$"):
        written = member.split("$")[1]
    else:
        written = re.split(r"[$-]", member, maxsplit=1)[0]
    return written or member


def declared_functions(text: str, suffix: str) -> set[str]:
    """The functions a source file declares, by name."""
    pattern = _KOTLIN_FUN if suffix == ".kt" else _JAVA_DECL
    return {m.group("name").strip("`") for m in re.finditer(pattern.pattern, text, re.M)}


def lambda_frame(symbol: str) -> bool:
    """A frame of a class the compiler numbered: a lambda's, or an anonymous object's."""
    owner = symbol.rpartition(".")[0].partition("$$")[0]
    nested = owner.split("$")[1:]
    return bool(nested) and nested[-1].isdigit()


def frame_function(symbol: str, declared: set[str] | None = None) -> str:
    """The source function a frame belongs to, seen through the compiler.

    A plain frame names it directly, and so does a frame of a class nested in
    another: `Repo$Companion.warm` is `warm`, whatever the companion, the
    `ViewHolder` or the `object` around it is called. Reading the first `$`
    segment as the function named the class instead, and `plan_from_anr` then
    refused every such frame as a line the compiler had moved — on a checkout
    that was the build that froze.

    A lambda's frame does not name it. Kotlin compiles one into a class of its
    own, entered through `invoke` or `invokeSuspend` and numbered last, so
    `Handler$updateLocality$2.invokeSuspend` was written inside
    `updateLocality`: the innermost segment that is a name rather than a
    number. Compiled with invokedynamic there is no class, and the method
    carries the function in its own name — see `_as_written`. A class the
    toolchain made rather than anyone here, everything from `$$` on, is read
    the same way as a lambda's.

    An anonymous class is numbered last too, and is entered through a method
    the source declares — `MainActivity$1.onClick`, or `Screen$load$1.onClick`
    for one made inside `load` — so the member is the answer there. A lambda
    compiled into a class for an interface of its own reads exactly like
    that: a `collect { }` block is `Screen$load$1$1.emit`. Its line then falls
    in `load` while this says `emit`, and the frame is refused rather than
    bracketed — the right way to be wrong, since a pair around `load` would
    time the call that set the block up and not the block.
    """
    owner, _, member = symbol.rpartition(".")
    written, made, _ = owner.partition("$$")
    nested = written.split("$")[1:]
    if made or (member in _LAMBDA_ENTRY and nested and nested[-1].isdigit()):
        named = [s for s in nested if s.isidentifier() and s not in _LAMBDA_ENTRY]
        # A lambda in a local variable's initializer is named after the
        # variable: `Repo$updateLocality$fresh$1`. With the file's functions
        # at hand, a segment it does not declare is passed over for the one
        # above it.
        if declared is not None:
            named = [s for s in named if s in declared] or named
        if named:
            return named[-1]
    return _as_written(member)


def through_lambda(symbol: str) -> bool:
    """Whether a frame was entered through a lambda rather than the function
    it was written in.

    `onCreate$lambda$0`, `$lambda-0`, javac's `lambda$flush$0`, or `invoke`
    and `invokeSuspend` on a class numbered last. A lambda with a frame of its
    own was not inlined, and it often runs later — a click listener, `post`,
    `launch` — so the function around it only registers it, and a pair there
    times the registering while the code that froze runs outside it.
    """
    owner, _, member = symbol.rpartition(".")
    nested = owner.partition("$$")[0].split("$")[1:]
    return (member.startswith("lambda$")
            or re.search(r"\$lambda[$-]\d+", member) is not None
            or (member in _LAMBDA_ENTRY and bool(nested) and nested[-1].isdigit()))


def _suspends(text: str, decl_line: int) -> bool:
    """Whether the declaration on this line is a `suspend fun`."""
    lines = text.splitlines()
    head = lines[decl_line - 1] if 0 < decl_line <= len(lines) else ""
    return re.search(r"\bsuspend\b[^(]*\bfun\b", head) is not None


def marker_for(symbol: str, prefix: str) -> str:
    """`pkg.Class$1.method` as `AGENTTMP_Class_1_method`.

    The package is dropped: a trace section name is read in a list of twenty
    and the last two parts are what tell them apart.
    """
    parts = symbol.split(".")
    tail = ".".join(parts[-2:]) if len(parts) > 1 else symbol
    return prefix + re.sub(r"\W+", "_", tail).strip("_")


def plan_from_anr(root: Path, frames: list[tuple[str, str, int | None]],
                  prefix: str = DEFAULT_PREFIX,
                  allowed: list[str] | None = None,
                  unplaced: int = 0, version: str | None = None) -> Plan:
    """A marker plan whose targets come from a stack rather than the manifest.

    `frames` is `(symbol, file relative to root, line)` — what `echolot anr`
    placed in this checkout. Nothing is searched for here; each frame already
    says where it is, and the work is finding the block around the line and
    deciding whether a begin/end pair can go in mechanically.

    `unplaced` and `version` are only for the note at the end: they are what
    lets a working tree from the wrong build be named as such instead of
    looking like a tool that refuses everything.
    """
    out = Plan(root=str(root), module=None, package=None)
    seen: set[str] = set()
    recurs: set[str] = set()
    for symbol, rel, line in frames:
        if line is None:
            out.notes.append(f"{symbol} — the frame carries no line, so there "
                             f"is nothing to find the block around")
            continue
        marker = marker_for(symbol, prefix)
        if marker in seen:
            # A function on the stack twice calls itself, and its one marker
            # will open inside itself. Said once, so the nesting in the trace
            # is not read as a second caller.
            if marker not in recurs:
                recurs.add(marker)
                out.notes.append(
                    f"{symbol} is on the stack more than once: it calls itself, "
                    f"so `{marker}` will open inside itself. The report counts "
                    f"the outermost one")
            continue
        seen.add(marker)

        path = root / rel
        try:
            text = read_source(path)
        except OSError:
            continue
        block = enclosing_block(text, path.suffix, line)
        if block is None:
            out.proposals.append(Proposal(
                "anr_frame", rel, line, f"{symbol.rsplit('.', 1)[-1]} — on the "
                f"stack when it froze", marker, "anr", gradle_module(path, root),
                applicable=False,
                reason="no function around that line that this can bracket"))
            continue

        name, decl_line, open_at, close_at, has_return = block

        # The line and the symbol can disagree, and when they do the line is
        # the one to distrust. On a live report a frame naming
        # `OrderManagerImpl.getActiveOrders` carried a line that falls inside
        # `getPlacedOrder` — R8 moves them, and inlining moves them further.
        # Bracketing the block the line landed in would have put a marker
        # named after one function around the body of another, and the trace
        # would then say that function took the time.
        declared = declared_functions(text, path.suffix)
        wanted = frame_function(symbol, declared)
        if name == wanted:
            disagree = ""
        elif lambda_frame(symbol) and wanted not in declared:
            # On purpose, and on the right build: see `frame_function`.
            disagree = (f"a lambda the compiler made into a class of its own "
                        f"(`{wanted}`), inside `{name}` — a pair around `{name}` "
                        f"would time the call that set it up; mark inside it by hand")
        elif wanted not in declared:
            disagree = (f"the frame names `{wanted}`, which this file does not "
                        f"declare — mark by hand")
        else:
            disagree = (f"the line falls inside `{name}` while the frame names "
                        f"`{wanted}` — the compiler moved it; mark by hand")
        flat = one_line_body(text, open_at, close_at)
        why = (disagree
               or (f"a lambda: a pair around `{name}` would time setting it up, "
                   f"and it runs later — mark inside the lambda by hand"
                   if through_lambda(symbol) else "")
               # `beginSection` and `endSection` act on the calling thread,
               # and a suspend function can resume on another: the marker
               # stays open on the first, the end closes someone else's
               # section, and on Main the wait is counted as its own time.
               or ("a suspend function: after it resumes, the end may run on "
                   "another thread — mark a stretch with no suspension point "
                   "by hand" if path.suffix == ".kt" and _suspends(text, decl_line)
                   else "")
               or _why_not(open_at, has_return, flat, close_at, text))
        out.proposals.append(Proposal(
            "anr_frame", rel, decl_line,
            f"{name} — on the stack when it froze, at line {line}",
            marker, "anr", gradle_module(path, root),
            applicable=not why, reason=why,
            open_at=open_at, close_at=close_at))

    # A frame outside `instrumentation.allowed` was dropped here without a
    # row, while `plan` shows such a site and refuses it. It is still on the
    # stack, and the frame under it may be the allowed caller to mark
    # instead — which a reader cannot see from a list it is missing from.
    for p in out.proposals:
        _refuse_outside(p, list(allowed or []))

    # A frame that lands on an import, on a blank line between two functions,
    # or past the end of the file is not a hard case — it is a line number
    # from another build. Refusing each one on its own merits and saying
    # nothing about the pattern reads as a tool that cannot do its job, when
    # what happened is that the checkout is not the version that froze.
    # Only what another build explains: a line in no function, or a frame
    # naming a function this file does not have. A lambda refused on purpose
    # and a line R8 moved into a neighbour happen on the build that froze.
    astray = sum(1 for p in out.proposals if not p.applicable
                 and (p.reason.startswith("no function")
                      or p.reason.startswith("the frame names")))
    total = len(out.proposals) + unplaced
    if total and (astray + unplaced) * 2 > total:
        out.notes.append(
            f"{astray + unplaced} of {total} frames land nowhere this checkout "
            f"recognises — on a line with no function, inside a different one, "
            f"or in a file that is not here. This working tree is probably not "
            f"the build that froze"
            + (f" ({version})" if version else "")
            + ". Check that build out before marking, or the markers measure "
              "something else.")
    return out


# --- naming the threads instead of marking the work ---------------------------
#
# A different question from everything above, and the one the other two cannot
# answer. `plan` asks where instrumentation usually belongs; `plan_from_anr`
# asks what was on the stack when it froze. Both start from a place in the
# code. This one starts from the opposite end: the report says a thread burned
# three seconds, the thread is called `pool-7-thread-1`, and nothing in the
# repository is called that — because the JDK named it, not the project.
#
# Marking the work is the wrong first move there. You do not know what the
# work is; that is the whole complaint. Naming the pool is cheaper and does
# not need to know: one edit at the place the pool is made covers everything
# that will ever run on it, and every detector already groups by thread name,
# so one round turns the whole report from `pool-7-thread-1` into `cart-queue`.
#
# Only then are markers worth placing, and by then you know where.

def _call_end(clean: str, open_paren: int, limit: int = 600, pair: str = "()") -> int:
    """Index just past the `)` closing the call whose `(` is at `open_paren`.

    Bounded: an unbalanced file must not drag the scan to the end of it.
    `pair="{}"` does the same for a lambda's braces.
    """
    opening, closing = pair
    depth = 0
    for i in range(open_paren, min(len(clean), open_paren + limit)):
        if clean[i] == opening:
            depth += 1
        elif clean[i] == closing:
            depth -= 1
            if depth == 0:
                return i + 1
    return min(len(clean), open_paren + limit)


def _with_trailing_lambda(clean: str, end: int) -> int:
    """Past a Kotlin trailing lambda that follows a call, else `end` as it is.

    `Executors.newFixedThreadPool(2) { r -> Thread(r, "io") }` hands over its
    ThreadFactory outside the parentheses, and the factory is what names the
    threads: read up to the `)` alone, that pool looked nameless.
    """
    k = end
    while k < len(clean) and clean[k] in " \t":
        k += 1
    if k < len(clean) and clean[k] == "{":
        return _call_end(clean, k, pair="{}")
    return end


def what_is_a_type(clean: str, m: re.Match) -> bool:
    """Whether a `Thread {` is a return type — `fun worker(): Thread {` — not a call."""
    if not m.group(0).endswith("{"):
        return False
    before = clean[:m.start()].rstrip()
    return before.endswith(":")


# Where each `Executors.new*` takes a ThreadFactory: the second argument of
# the two that take a size first, any argument of the three that take
# nothing else. `newWorkStealingPool` takes none.
_FACTORY_AT = {"FixedThreadPool": 2, "ScheduledThreadPool": 2, "CachedThreadPool": 1,
               "SingleThreadExecutor": 1, "SingleThreadScheduledExecutor": 1}


def _top_args(clean: str, open_paren: int, end: int) -> int:
    """How many arguments a call's top level holds."""
    depth, args, current = 0, 0, False
    for c in clean[open_paren + 1:end - 1]:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            args += 1
            current = False
            continue
        if not c.isspace():
            current = True
    return args + (1 if current else 0)


def _factory_given(clean: str, m: re.Match, end: int) -> bool:
    """Whether an `Executors.new*` call is handed a ThreadFactory, by position."""
    at = _FACTORY_AT.get(m.group(1))
    return at is not None and _top_args(clean, m.end() - 1, end) >= at


def _names_its_thread(clean: str, quoted: str, open_paren: int, end: int) -> bool:
    """Whether a `Thread(…)` call is handed a name among its own arguments.

    Read at the top level of the argument list, where a name goes, on two
    views of the file: `clean` for the structure, and `quoted` — comments
    blanked, strings kept — to see a string where there is one. Everything
    used to be read on `clean` alone, which blanks `"io"` out of
    `Thread(r, "io")` before anything looks for a name, so a thread named in
    plain sight was reported as nameless.

    A string at that level is the name: `Thread("sync")`, `Thread(r, "io-$n")`.
    So is a second argument of any kind — `Thread(r, name)` inside a factory
    that was handed its name — because every constructor of the JDK's that
    takes two or more arguments takes a name, except `(ThreadGroup,
    Runnable)`, which an app rarely has reason to call. A string nested
    deeper, in the Runnable, is not a name, and a Kotlin trailing lambda is
    the Runnable and not an argument counted here.
    """
    depth, args, current = 0, 0, False
    for i in range(open_paren + 1, end - 1):
        c = clean[i]
        if depth == 0 and quoted[i] == '"':
            return True
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            args += 1 if current else 0
            current = False
            continue
        if not c.isspace():
            current = True
    return args + (1 if current else 0) >= 2


def plan_pools(root: Path, allowed: list[str] | None = None) -> Plan:
    """Where a thread or pool is created with the JDK's default naming.

    Nothing here is applicable. The tool can find the site — `Executors.new*`
    is an exact string like `Room.databaseBuilder` — and it cannot write the
    name. A name derived from the surrounding code came out as
    `provideproductr`, `onauthenticatio` and `capacity` on two real projects:
    the enclosing symbol is a Dagger provider or a local variable, and what
    the pool is *for* is in the call it is passed to. That is a sentence to
    read, not a pattern to match.
    """
    root = root.resolve()
    allowed = list(allowed or [])
    out = Plan(root=str(root), module=None, package=None)

    for path in source_files(root):
        rel = _rel(path, root)
        try:
            text = read_source(path)
        except OSError:
            continue
        clean = strip_noise(text)
        quoted: str | None = None   # made the first time a `Thread(` needs it
        for rx, name_of in ((_EXECUTORS, lambda m: f"Executors.new{m.group(1)}"),
                            (_POOL_CTOR, lambda m: m.group(1)),
                            (_BARE_THREAD, lambda m: "Thread"),
                            (_THREAD_LAMBDA, lambda m: "Thread { }")):
          for m in rx.finditer(clean):
            if what_is_a_type(clean, m):
                continue
            # The argument list, not the line. A naming factory is routinely
            # passed on a continuation line — `newFixedThreadPool(\n  2,
            # ThreadFactoryBuilder()…)` — and a per-line check called that an
            # unnamed pool.
            what = name_of(m)
            if what == "Thread { }":
                # The trailing lambda is the Runnable, and nothing names it.
                end = _call_end(clean, m.end() - 1, pair="{}")
            else:
                end = _call_end(clean, m.end() - 1)
            if what.startswith("Executors.new") and _factory_given(clean, m, end):
                # A ThreadFactory handed over: the JDK's default name is not
                # what its threads get, whatever the factory does.
                continue
            if what == "Thread":
                if quoted is None:
                    quoted = strip_noise(text, strings=False)
                if _names_its_thread(clean, quoted, m.end() - 1, end):
                    continue
            elif what != "Thread { }":
                # A factory that builds its threads with `Thread(` is judged
                # by that call, which this loop reaches on its own: named, the
                # pool's threads are named; not, the `Thread(` is the row, and
                # the place to name them. A row for the pool as well would
                # point at the same line with the wrong default name.
                end = _with_trailing_lambda(clean, end)
                if _BARE_THREAD.search(clean, m.end(), end):
                    continue
            if _NAMED_ALREADY.search(clean[m.start():end]):
                continue
            line_no = clean.count("\n", 0, m.start()) + 1
            kind = "thread_name" if what.startswith("Thread") else "pool_name"
            # ForkJoin's worker factory names its own: `ForkJoinPool-1-worker-1`.
            born = ("Thread-N" if kind == "thread_name"
                    else "ForkJoinPool-N-worker-M" if what in ("ForkJoinPool",
                                                                 "Executors.newWorkStealingPool")
                    else "pool-N-thread-M")
            p = Proposal(
                kind, rel, line_no,
                f"{what} — its threads reach the trace as `{born}`, and the "
                f"report groups by that",
                # There is no marker to write here, and guessing a name is
                # what this refuses to do.
                "(name it)", "jdk", gradle_module(path, root),
                applicable=False, reason="")
            # Naming is not a marker, so "the nearest allowed caller" is no
            # advice here; that the place is outside is the whole reason.
            _refuse_outside(p, allowed, "outside instrumentation.allowed")
            out.proposals.append(p)

    out.proposals.sort(key=lambda p: (p.file, p.line))
    if not out.proposals:
        out.notes.append(
            "no thread or pool is created with the JDK's default naming under "
            "this root — every thread in the trace already carries a name "
            "somebody chose, so a nameless one comes from a library or from "
            "the platform")
    else:
        out.notes.append(
            f"give each one a name of at most {COMM_MAX} characters — Linux "
            f"truncates the rest, and the trace carries what is left")
        out.notes.append(
            "naming a pool is not instrumentation and leaves no `AGENTTMP_` "
            "behind: it changes what the existing report calls a row. Mark the "
            "work inside only after the rows have names")
    if len(out.proposals) > CAP:
        out.hidden = len(out.proposals) - CAP
        out.proposals = out.proposals[:CAP]
    return out


# --- apply / remove ------------------------------------------------------------

def _indent_of(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    line = text[start:offset]
    return line[:len(line) - len(line.lstrip())]


def _inner_indent(text: str, open_at: int) -> str:
    """The indentation of the first non-empty line after `{`, else +4."""
    nl = text.find("\n", open_at)
    while nl >= 0:
        end = text.find("\n", nl + 1)
        line = text[nl + 1:end if end >= 0 else len(text)]
        if line.strip():
            return line[:len(line) - len(line.lstrip())]
        nl = end
    return _indent_of(text, open_at) + "    "


def _ending_before(text: str, at: int) -> str:
    """The line ending just before `at`, a line start: what a new line there ends with.

    Taken from the line above rather than decided for the whole file, so a
    CRLF file gets CRLF lines and a file that mixes the two gets whatever its
    neighbour has — either way, deleting the line leaves the rest as it was.
    """
    return "\r\n" if text[max(0, at - 2):at] == "\r\n" else "\n"


def apply(root: Path, pl: Plan) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Insert begin/end pairs for the applicable proposals.

    Returns (edited files with their markers, files that could not be read).

    A begin line under the line holding the block's `{`, an end line over
    the line holding its `}`, both tagged so `remove` can find them without
    any bookkeeping. Whole lines only, and only between two of the file's
    own: `brace_lines` refuses a block whose braces share their lines with
    code, and the plan has said so already. The lines are joined in one pass
    from the top, with each one ending the way the line above it ends, and
    the file is written back as bytes — nothing else in it changes, its line
    endings included. Java gets a `;`, Kotlin does not.

    A file that is not valid UTF-8 is skipped and named. `plan` reads with
    `errors="replace"` and computes its offsets on what that produced; this
    used to read the same file strictly and come out of the CLI as a
    `UnicodeDecodeError` traceback. Reading it leniently here instead would be
    worse than the crash: an invalid sequence collapses to one replacement
    character, every offset after it shifts, and the markers would go in at
    the wrong places in a file nobody would think to re-check. `remove`
    already skips such a file; this is the other half of that.
    """
    root = root.resolve()
    by_file: dict[str, list[Proposal]] = {}
    for p in pl.proposals:
        if p.applicable and p.open_at is not None and p.close_at is not None:
            by_file.setdefault(p.file, []).append(p)
    done: list[tuple[str, list[str]]] = []
    unreadable: list[str] = []
    for rel in sorted(by_file):
        path = root / rel
        try:
            text = read_source(path, strict=True)
        except (OSError, UnicodeDecodeError):
            unreadable.append(rel)
            continue
        semi = ";" if path.suffix == ".java" else ""
        # (offset, 0 for a begin and 1 for an end, the line): sorted, a begin
        # and an end at one offset — an empty body — come out in that order.
        edits: list[tuple[int, int, str]] = []
        marked = []
        blocks: set[tuple[int, int]] = set()
        for p in by_file[rel]:
            if TAG in text[p.open_at:p.close_at + 1] or (p.open_at, p.close_at) in blocks:
                continue   # already marked here
            room = brace_lines(text, p.open_at, p.close_at)
            if isinstance(room, str):
                continue   # refused, and the plan printed why — see brace_lines
            begin_at, end_at = room
            blocks.add((p.open_at, p.close_at))
            marked.append(p.marker)
            ind = _inner_indent(text, p.open_at)
            edits.append((begin_at, 0, f"{ind}android.os.Trace.beginSection(\"{p.marker}\")"
                                       f"{semi} {TAG}{_ending_before(text, begin_at)}"))
            edits.append((end_at, 1, f"{ind}android.os.Trace.endSection(){semi} {TAG}"
                                     f"{_ending_before(text, end_at)}"))
        if not edits:
            continue
        pieces, last = [], 0
        for offset, _, line in sorted(edits):
            pieces += [text[last:offset], line]
            last = offset
        pieces.append(text[last:])
        path.write_bytes("".join(pieces).encode("utf-8"))
        done.append((rel, marked))
    return done, unreadable


def remove(root: Path) -> tuple[list[tuple[str, int]], list[tuple[str, int, str]]]:
    """Delete every line `apply` wrote, under root.

    Returns (files edited, with the number of lines taken out of each; lines
    that carry the tag and were left in place, as file, line number after
    this pass, and the line itself).

    A line goes only when it has exactly the shape `apply` writes — see
    `_APPLIED_LINE` — and it goes whole, line ending and all, so the lines
    around it and their endings are the ones the file had before `--apply`.

    A line that carries the tag in any other shape stays. It is somebody's
    code with the tag typed onto it, or a line an older `apply` mangled —
    `beginSection(…) // echolot:mark AppTheme {` — and deleting it would
    take that code along. It is handed back instead of passed over: kept in
    silence, it let the command say "no `echolot:mark` lines found" about a
    tree that still had them.
    """
    root = root.resolve()
    touched: list[tuple[str, int]] = []
    kept: list[tuple[str, int, str]] = []
    for p in source_files(root):
        try:
            text = read_source(p, strict=True)
        except (OSError, UnicodeDecodeError):
            continue
        if TAG not in text:
            continue
        lines = text.split("\n")
        rest = [ln for ln in lines if not is_applied_line(ln)]
        rel = _rel(p, root)
        kept += [(rel, n, ln.strip()) for n, ln in enumerate(rest, 1) if TAG in ln]
        if len(rest) < len(lines):
            p.write_bytes("\n".join(rest).encode("utf-8"))
            touched.append((rel, len(lines) - len(rest)))
    return touched, kept


# --- rendering -------------------------------------------------------------------

def render(pl: Plan) -> list[str]:
    out: list[str] = []
    if pl.module:
        out.append(f"# app module {pl.module}" + (f" · package {pl.package}" if pl.package else ""))
    for a in pl.ambiguity:
        out.append(f"! {a}")
    if not pl.proposals and not pl.ambiguity:
        out.append("# nothing to mark from the platform's vocabulary in this tree")
    if pl.proposals:
        out.append("")
        w_file = max(len(f"{p.file}:{p.line}") for p in pl.proposals)
        w_marker = max(len(p.marker) for p in pl.proposals)
        for p in pl.proposals:
            flag = "+" if p.applicable else "·"
            out.append(f"{flag} {f'{p.file}:{p.line}'.ljust(w_file)}  {p.marker.ljust(w_marker)}  "
                       f"[{p.source}]  {p.what}")
            if p.reason:
                out.append(f"  {' ' * w_file}  {p.reason}")
        if pl.hidden:
            out.append(f"  … and {pl.hidden} more (the cap is {CAP}; a skeleton, not a survey)")
        out.append("")
        n_apply = sum(1 for p in pl.proposals if p.applicable)
        if any(p.kind in ("pool_name", "thread_name") for p in pl.proposals):
            # Nothing here is a begin/end pair, so naming `--apply` would send
            # a reader to a flag with nothing to do.
            out.append("# · = a place to name, by hand — there is no marker to insert")
        else:
            out.append(f"# + = `echolot mark --apply` puts a begin/end pair there ({n_apply}); "
                       f"· = proposed, mark by hand")
    if pl.notes:
        out.append("")
        for n in pl.notes:
            out.append(f"# note: {n}")
    return out
