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

Nothing here matches `*ViewModel`, `*Repository`, `*Screen` or any other
convention. Every proposal carries its source, so a reader can see how firm
the ground is; what cannot be found is said as not found, never guessed;
the output is sorted and byte-for-byte the same for the same tree.

Applying is mechanical and reversible: each inserted line is a whole line of
its own ending in the tag `// echolot:mark`, and `--remove` deletes exactly
those lines. Both halves of that sentence are load-bearing, so a block that
cannot take a line of its own is refused rather than approximated:

    a `return` in the body   the end would be skipped;
    a body written on one    the begin and end lines would cross, and the
    line                     body would end up inside the begin line's
                             comment — where `--remove` would then delete it.

Both are reported as "mark by hand" and shown with the reason.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .domains import gradle_module

SOURCE_EXT = (".kt", ".java")
SKIP_DIRS = {"build", ".git", ".gradle", "generated", ".echolot", "node_modules"}
DEFAULT_PREFIX = "AGENTTMP_"
TAG = "// echolot:mark"
# Exactly what `apply` writes, and nothing else. `remove` deletes whole lines,
# so a rule as loose as "the tag is somewhere in it" takes the line's real code
# along — which is how a one-line block used to be destroyed rather than merely
# mangled. A hand-written tag on a line of code is now left alone and reported
# as such rather than quietly deleted.
_APPLIED_LINE = re.compile(
    r"^\s*android\.os\.Trace\.(?:begin|end)Section\s*\([^)]*\)\s*;?\s*"
    + re.escape(TAG) + r"\s*$")
CAP = 7   # proposals shown; the rest is a count. Five to seven is a skeleton, not a survey.

# --- the vocabulary -----------------------------------------------------------

_LAUNCHER_ACTIVITY = re.compile(
    r"<activity(?:-alias)?\b(?P<attrs>[^>]*)>(?P<body>.*?)</activity(?:-alias)?>", re.S)
_ANDROID_NAME = re.compile(r"android:name\s*=\s*\"([^\"]+)\"")
_TARGET_ACTIVITY = re.compile(r"android:targetActivity\s*=\s*\"([^\"]+)\"")
_ACTION_MAIN = re.compile(r"android\.intent\.action\.MAIN")
_CATEGORY_LAUNCHER = re.compile(r"android\.intent\.category\.LAUNCHER")
_APPLICATION_TAG = re.compile(r"<application\b([^>]*)>", re.S)
_MANIFEST_PACKAGE = re.compile(r"<manifest\b[^>]*\bpackage\s*=\s*\"([^\"]+)\"", re.S)
_NAMESPACE = re.compile(r"\b(?:namespace|applicationId)\s*(?:=|\s)\s*[\"']([^\"']+)[\"']")

# Kotlin `override fun onCreate(` / Java `protected void onCreate(`.
_ON_CREATE = re.compile(
    r"^[ \t]*(?:override\s+)?(?:public\s+|protected\s+|private\s+)?(?:override\s+)?"
    r"(?:fun|void)\s+onCreate\s*\(", re.M)
_SET_CONTENT = re.compile(r"\bsetContent\s*(?:\([^)]*\)\s*)?\{")
_SET_CONTENT_VIEW = re.compile(r"\bsetContentView\s*\(")
_ROOM_BUILDER = re.compile(r"\bRoom\s*\.\s*(?:databaseBuilder|inMemoryDatabaseBuilder)\s*\(")
_KOIN_START = re.compile(r"\bstartKoin\s*\{")
_HILT_APP = re.compile(r"@HiltAndroidApp\b")
_CLASS_DECL = re.compile(r"\b(?:class|object)\s+([A-Za-z_][A-Za-z0-9_]*)")
_FUN_DECL = re.compile(r"\bfun\s+(?:<[^>]*>\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_RUNTIME_TRACING = re.compile(r"runtime[-.]tracing")
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?=[({])")
_KEYWORDS = {"if", "for", "while", "when", "try", "catch", "finally", "return", "else",
             "do", "run", "let", "also", "apply", "with", "repeat", "synchronized",
             "throw", "object", "fun", "class", "val", "var", "super", "this"}


def is_applied_line(line: str) -> bool:
    """A line `--apply` wrote: safe for `--remove` to delete whole."""
    return bool(_APPLIED_LINE.match(line))


@dataclass
class Proposal:
    kind: str                 # app_oncreate | activity_oncreate | set_content | set_content_view | compose_root | room_open | di_koin
    file: str                 # relative to root
    line: int                 # 1-based
    what: str                 # for a human
    marker: str               # AGENTTMP_…
    source: str               # manifest+lifecycle | lifecycle | api | call-from-setContent
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

def strip_noise(text: str) -> str:
    """Strings and comments replaced by spaces, length and newlines kept.

    Brace matching and `return` detection run on this view, so a `}` inside
    a string literal or a `// return early` comment does not count.
    """
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif text.startswith('"""', i):
            j = text.find('"""', i + 3)
            j = n if j < 0 else j + 3
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c == '"' or c == "'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                if text[j] == "\\":
                    j += 1
                j += 1
            j = min(n, j + 1)
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


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

    `apply` puts the begin line just after the `{` and the end line at the
    start of the `}`'s line. When those are the same line the second insert
    lands *before* the first: the end marker comes out above the block, and
    the body is swallowed by the begin line's trailing `// echolot:mark`.
    `remove` then deletes that line whole and the body goes with it, which is
    the one thing this module promises never to do.

    `setContent { AppRoot() }` is the shape a great deal of Compose is written
    in, so this is not a corner. Refused and said out loud, the way a `return`
    in the body already is.
    """
    if open_at is None or close_at is None:
        return False
    return "\n" not in text[open_at:close_at]


def _why_not(open_at: int | None, has_return: bool, flat: bool) -> str:
    """Why a block cannot take a begin/end pair mechanically. Empty when it can."""
    if open_at is None:
        return "no block body found"
    if has_return:
        return "has a return in its body — mark by hand"
    if flat:
        return "the whole body is on one line — split the block, or mark by hand"
    return ""


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# --- discovery -----------------------------------------------------------------

def source_files(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        if p.suffix in SOURCE_EXT and p.is_file() and not (SKIP_DIRS & set(p.parts)) \
                and "src" in p.parts:
            out.append(p)
    return out


def manifests(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("AndroidManifest.xml"))
            if not (SKIP_DIRS & set(p.parts)) and "main" in p.parts]


def launcher_activities(text: str) -> list[str]:
    """Class names of activities whose intent-filter has MAIN and LAUNCHER."""
    out = []
    for m in _LAUNCHER_ACTIVITY.finditer(text):
        body = m.group("body")
        if _ACTION_MAIN.search(body) and _CATEGORY_LAUNCHER.search(body):
            attrs = m.group("attrs")
            target = _TARGET_ACTIVITY.search(attrs)
            name = _ANDROID_NAME.search(attrs)
            chosen = target or name
            if chosen:
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


def simple_name(class_ref: str) -> str:
    return class_ref.rsplit(".", 1)[-1]


def find_class_file(root: Path, module_dir: Path | None, name: str,
                    files: list[Path]) -> Path | None:
    """The source file declaring `class <name>`, the manifest's module first."""
    pat = re.compile(r"\b(?:class|object)\s+" + re.escape(name) + r"\b")
    hits = []
    for p in files:
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if pat.search(t):
            hits.append(p)
    if not hits:
        return None
    if module_dir is not None:
        inside = [p for p in hits if module_dir in p.parents]
        if inside:
            return inside[0]
    return hits[0]


def find_on_create(text: str) -> tuple[int, int | None, int | None, bool] | None:
    """(line, open_at, close_at, has_return) of the first onCreate override."""
    m = _ON_CREATE.search(text)
    if not m:
        return None
    clean = strip_noise(text)
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
    body = clean[j + 1:k]
    has_return = re.search(r"\breturn\b", body) is not None
    return (line_of(text, m.start()), j, k, has_return)


def find_lambda(text: str, rx: re.Pattern) -> tuple[int, int | None, int | None] | None:
    """(line, open_at, close_at) of the first `name { … }` matched by rx."""
    m = rx.search(text)
    if not m:
        return None
    clean = strip_noise(text)
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


def find_composable_decl(name: str, files: list[Path]) -> tuple[Path, int] | None:
    """`@Composable fun <name>(` in this project's sources — the annotation is
    the platform's word for it, so a project function that merely shares a
    name with a library call is not mistaken for a screen."""
    pat = re.compile(r"@Composable\b[^{;]*?\bfun\s+(?:<[^>]*>\s*)?" + re.escape(name) + r"\s*\(", re.S)
    for p in files:
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = pat.search(t)
        if m:
            return p, line_of(t, m.end() - 1)
    return None


def under_allowed(rel: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    return any(rel == a.rstrip("/") or rel.startswith(a.rstrip("/") + "/") for a in allowed)


# --- the plan ------------------------------------------------------------------

def plan(root: Path, package: str | None = None, allowed: list[str] | None = None,
         prefix: str = DEFAULT_PREFIX, module: str | None = None) -> Plan:
    root = root.resolve()
    allowed = list(allowed or [])
    files = source_files(root)
    out = Plan(root=str(root), module=None, package=package)

    # 1. the app module: the manifest with a launcher activity
    candidates = []
    for mf in manifests(root):
        text = mf.read_text(encoding="utf-8", errors="replace")
        launchers = launcher_activities(text)
        if not launchers:
            continue
        mdir = module_dir_of(mf)
        candidates.append((mf, mdir, text, launchers, module_package(mdir, text)))
    if module:
        candidates = [c for c in candidates
                      if gradle_module(c[0], root) == module or _rel(c[1], root) == module.strip(":").replace(":", "/")]
    if len(candidates) > 1 and package:
        narrowed = [c for c in candidates if c[4] == package]
        if len(narrowed) == 1:
            candidates = narrowed
    if not candidates:
        out.notes.append("no launcher Activity in any AndroidManifest.xml under src/main — "
                         "this tree has no app entry point to mark (a library, or the app "
                         "module lives elsewhere: pass --root)")
        return out
    if len(candidates) > 1:
        out.ambiguity.append(
            "several modules declare a launcher Activity: "
            + ", ".join(f"{gradle_module(c[0], root)} ({c[4] or 'package unknown'})" for c in candidates)
            + " — pass --module, or set project.package so one matches")
        return out
    mf, mdir, mtext, launchers, pkg = candidates[0]
    out.module = gradle_module(mf, root)
    out.package = out.package or pkg
    if len(launchers) > 1:
        out.ambiguity.append(
            f"{_rel(mf, root)} declares {len(launchers)} launcher activities: "
            + ", ".join(launchers) + " — the first is taken; pass --module or edit the manifest")

    def add(p: Proposal) -> None:
        if not under_allowed(p.file, allowed):
            p.applicable = False
            p.reason = (p.reason + "; " if p.reason else "") + \
                "outside instrumentation.allowed — mark the nearest allowed caller instead"
        out.proposals.append(p)

    # 2. Application.onCreate
    app_cls = application_class(mtext)
    if app_cls:
        f = find_class_file(root, mdir, simple_name(app_cls), files)
        if f is None:
            out.notes.append(f"Application class {app_cls} is declared in the manifest but no "
                             f"source declares it under src/ (generated, or in a dependency)")
        else:
            t = f.read_text(encoding="utf-8", errors="replace")
            oc = find_on_create(t)
            if oc is None:
                out.notes.append(f"{_rel(f, root)}: {simple_name(app_cls)} does not override "
                                 f"onCreate — nothing of yours runs at bindApplication")
            else:
                line, o, c, ret = oc
                flat = one_line_body(t, o, c)
                add(Proposal("app_oncreate", _rel(f, root), line,
                             f"{simple_name(app_cls)}.onCreate — what runs inside bindApplication",
                             prefix + "app_oncreate", "manifest+lifecycle",
                             gradle_module(f, root),
                             applicable=o is not None and not ret and not flat,
                             reason=_why_not(o, ret, flat),
                             open_at=o, close_at=c))
    else:
        out.notes.append("no custom Application class in the manifest — bindApplication is "
                         "the framework's alone")

    # 3. launcher Activity: onCreate, setContent / setContentView
    act = launchers[0]
    f = find_class_file(root, mdir, simple_name(act), files)
    if f is None:
        out.notes.append(f"launcher Activity {act} is declared in the manifest but no source "
                         f"declares it under src/ (generated, or in a dependency)")
    else:
        t = f.read_text(encoding="utf-8", errors="replace")
        oc = find_on_create(t)
        if oc is None:
            m = re.search(r"\bclass\s+" + re.escape(simple_name(act)) + r"\b[^{]*?:\s*([A-Za-z_][\w.]*)", t)
            base = m.group(1) if m else None
            out.notes.append(
                f"{_rel(f, root)}: {simple_name(act)} does not override onCreate"
                + (f" — it inherits from {base}; the override, if any, is there" if base else ""))
        else:
            line, o, c, ret = oc
            flat = one_line_body(t, o, c)
            add(Proposal("activity_oncreate", _rel(f, root), line,
                         f"{simple_name(act)}.onCreate — the launcher Activity, what runs inside activityStart",
                         prefix + "activity_oncreate", "manifest+lifecycle",
                         gradle_module(f, root),
                         applicable=o is not None and not ret and not flat,
                         reason=_why_not(o, ret, flat),
                         open_at=o, close_at=c))
        sc = find_lambda(t, _SET_CONTENT)
        if sc:
            line, o, c = sc
            flat = one_line_body(t, o, c)
            add(Proposal("set_content", _rel(f, root), line,
                         "setContent { } — the root of the Compose tree; recomposition re-enters it",
                         prefix + "set_content", "api", gradle_module(f, root),
                         applicable=o is not None and c is not None and not flat,
                         reason=_why_not(o, False, flat) if o is None or flat else "",
                         open_at=o, close_at=c, lambda_body=True))
            # one hop: what setContent calls, when it is this project's code
            if o is not None and c is not None:
                clean = strip_noise(t)
                found = 0
                for name in calls_inside(clean[o + 1:c]):
                    hit = find_composable_decl(name, files)
                    if hit is None:
                        continue
                    hf, hl = hit
                    add(Proposal("compose_root", _rel(hf, root), hl,
                                 f"@Composable {name}() — called from setContent, defined here",
                                 prefix + "compose_" + name, "call-from-setContent",
                                 gradle_module(hf, root), applicable=False,
                                 reason="a composable: wrap its call site by hand, or use "
                                        "androidx.compose.runtime:runtime-tracing (see notes)"))
                    found += 1
                    if found >= 3:
                        break
        else:
            m = _SET_CONTENT_VIEW.search(t)
            if m:
                add(Proposal("set_content_view", _rel(f, root), line_of(t, m.start()),
                             "setContentView(…) — the View hierarchy is inflated here",
                             prefix + "set_content_view", "api", gradle_module(f, root),
                             applicable=False, reason="a call, not a block — mark the "
                             "surrounding onCreate instead (proposed above)"))

    # 4. Room, Koin, Hilt — API strings anywhere in the sources
    for p in files:
        rel = _rel(p, root)
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _ROOM_BUILDER.finditer(t):
            add(Proposal("room_open", rel, line_of(t, m.start()),
                         "Room.databaseBuilder — the database is opened here",
                         prefix + "room_open", "api", gradle_module(p, root),
                         applicable=False,
                         reason="a builder chain — wrap the enclosing function by hand"))
        for m in _KOIN_START.finditer(t):
            add(Proposal("di_koin", rel, line_of(t, m.start()),
                         "startKoin { } — the DI graph is built here",
                         prefix + "di_koin", "api", gradle_module(p, root),
                         applicable=False, reason="mark the enclosing function by hand"))
        if _HILT_APP.search(t) and not any("Hilt" in n for n in out.notes):
            out.notes.append(f"{rel}: @HiltAndroidApp — the graph is generated; its cost sits "
                             f"inside Application.onCreate (super.onCreate), nothing separate to mark")

    # 5. what would give names for free
    tracing = False
    for name in ("build.gradle.kts", "build.gradle"):
        bf = mdir / name
        if bf.exists() and _RUNTIME_TRACING.search(bf.read_text(encoding="utf-8", errors="replace")):
            tracing = True
    if not tracing:
        out.notes.append("androidx.compose.runtime:runtime-tracing is not among the app module's "
                         "dependencies — with it, composable names appear in the trace with no "
                         "markers at all; the first thing to add for a Compose app")

    # deterministic order: by kind rank, then path, then line; then the cap
    rank = {k: i for i, k in enumerate(("app_oncreate", "activity_oncreate", "set_content",
                                        "set_content_view", "compose_root", "room_open", "di_koin"))}
    out.proposals.sort(key=lambda p: (rank.get(p.kind, 99), p.file, p.line))
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


def apply(root: Path, pl: Plan) -> list[tuple[str, list[str]]]:
    """Insert begin/end pairs for the applicable proposals. Returns (file, markers).

    A begin line right after the block's `{`, an end line right before its
    `}`, both tagged so `remove` can find them without any bookkeeping.
    Files are edited from the last offset backwards, so earlier offsets stay
    valid. Java gets a `;`, Kotlin does not.
    """
    root = root.resolve()
    by_file: dict[str, list[Proposal]] = {}
    for p in pl.proposals:
        if p.applicable and p.open_at is not None and p.close_at is not None:
            by_file.setdefault(p.file, []).append(p)
    done = []
    for rel in sorted(by_file):
        path = root / rel
        text = path.read_text(encoding="utf-8")
        semi = ";" if path.suffix == ".java" else ""
        edits = []   # (offset, insert_text)
        marked = []
        for p in by_file[rel]:
            if TAG in text[p.open_at:p.close_at + 1]:
                continue   # already marked here
            if one_line_body(text, p.open_at, p.close_at):
                continue   # the two inserts would cross — see one_line_body
            marked.append(p.marker)
            ind = _inner_indent(text, p.open_at)
            begin = f"\n{ind}android.os.Trace.beginSection(\"{p.marker}\"){semi} {TAG}"
            end = f"{ind}android.os.Trace.endSection(){semi} {TAG}\n"
            edits.append((p.open_at + 1, begin))
            # before the closing brace, at the start of its line
            line_start = text.rfind("\n", 0, p.close_at) + 1
            edits.append((line_start, end))
        if not edits:
            continue
        for offset, ins in sorted(edits, key=lambda e: -e[0]):
            text = text[:offset] + ins + text[offset:]
        path.write_text(text, encoding="utf-8")
        done.append((rel, marked))
    return done


def remove(root: Path) -> list[tuple[str, int]]:
    """Delete every line tagged by apply, under root. Returns (file, lines removed)."""
    root = root.resolve()
    touched = []
    for p in source_files(root):
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if TAG not in text:
            continue
        lines = text.split("\n")
        kept = [ln for ln in lines if not is_applied_line(ln)]
        removed = len(lines) - len(kept)
        if removed:
            p.write_text("\n".join(kept), encoding="utf-8")
            touched.append((_rel(p, root), removed))
    return touched


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
        out.append(f"# + = `echolot mark --apply` puts a begin/end pair there ({n_apply}); "
                   f"· = proposed, mark by hand")
    if pl.notes:
        out.append("")
        for n in pl.notes:
            out.append(f"# note: {n}")
    return out
