"""The slice-to-code map, built from the sources.

`domains` is the central abstraction of the config: it turns a name from the
report into a hypothesis without scanning the repository blindly. And blind
scanning is the main context eater in the naive approach.

It can be assembled mechanically: a slice name is a string literal inside a
tracing call, it survives minification, and it is found by exact search. What
is left for a human is fixing the wording, not searching.

The second answer matters just as much: **coverage**. With little or no
instrumentation there is nothing to attach findings to, and saying so up front
is cheaper than discovering it on the loop's third round.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

SOURCE_EXT = (".kt", ".java")

# Directories a source walk has no business entering. The union of what the
# three walkers used to carry separately, plus the ones none of them named:
# a checkout can hold a virtualenv or a node_modules, and neither has Kotlin
# in it.
SKIP_DIRS = {
    "build", ".git", ".gradle", "generated", ".echolot", "node_modules",
    ".venv", "venv", ".idea", "__pycache__", ".tox",
    # The agent layer: markdown for a model to read, never application source.
    ".claude",
}


def source_files(root: Path, *, under_src: bool = False) -> list[Path]:
    """Every Kotlin and Java source under the root, in a stable order.

    One walker for `domains`, `mark` and `anr`. All three wrote the same
    `sorted(root.rglob("*"))` and then dropped what they did not want — which
    reads the whole tree first and discards afterwards, so `.git` and
    `node_modules` were walked in full every time to yield nothing. `os.walk`
    can be told not to go in, and that is the difference between reading a
    checkout and reading a checkout plus everything anyone ever installed
    into it.

    `under_src` is `mark`'s extra rule: it only proposes markers for files
    under a `src/` directory.

    Sorted at the end rather than per directory, so the order is exactly what
    the global `sorted(rglob(...))` produced — `domains` names the first site
    of each slice name in its map, and that must not move.
    """
    return _walk(root, lambda n: n.endswith(SOURCE_EXT), under_src=under_src)


def files_named(root: Path, name: str) -> list[Path]:
    """Every file with this name, from the same pruned walk.

    `mark` looks for AndroidManifest.xml and used `rglob` for it, which walks
    everything the source walk is careful not to enter.
    """
    return _walk(root, lambda n: n == name)


def files_ending(root: Path, suffix: str) -> list[Path]:
    """Every file whose name ends with this, from the same pruned walk."""
    return _walk(root, lambda n: n.endswith(suffix))


def _is_worktree(path: Path) -> bool:
    """A second checkout of the same repository, parked inside it.

    Claude Code keeps its worktrees under `.claude/worktrees/`, and a worktree
    is a full copy of the tree. Walking into one counts every module twice:
    the coverage report lists `:app` and `:.claude:worktrees:x:app` side by
    side, and `mark` sees two modules declaring the same launcher Activity and
    refuses to pick one without `--module`.

    A worktree carries a `.git` file pointing into `.git/worktrees/`. A
    submodule's `.git` file points into `.git/modules/` and is part of the
    build — its sources are the project's own, so it is walked like the rest.
    """
    marker = path / ".git"
    try:
        if not marker.is_file():
            return False
        head = marker.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return False
    return "/worktrees/" in head.replace("\\", "/")


def _walk(root: Path, wanted, *, under_src: bool = False) -> list[Path]:
    found: list[Path] = []
    for base, dirs, names in os.walk(root):
        here = Path(base)
        dirs[:] = [d for d in dirs
                   if d not in SKIP_DIRS and not _is_worktree(here / d)]
        if under_src and "src" not in here.parts:
            continue
        found += [here / n for n in names if wanted(n)]
    return sorted(found)

# Qualified forms are unambiguous and count everywhere.
_QUALIFIED = re.compile(
    r'\b(?:Trace|TraceCompat)\s*\.\s*'
    r'(?:beginSection|beginAsyncSection)\s*\(\s*"((?:[^"\\]|\\.)*)"'
)
# Bare trace("...") and traceAsync("...", …) count only where androidx.tracing
# is imported; otherwise any logging function with that name would land in
# the map.
_BARE = re.compile(r'\btrace(?:Async)?\s*\(\s*"((?:[^"\\]|\\.)*)"')
_BARE_IMPORT = re.compile(r'^\s*import\s+androidx\.tracing', re.MULTILINE)

# A call with a non-literal argument: the name is assembled at runtime and
# cannot be recovered statically. Worth counting — such sites are visible in
# the trace but will never appear in the map. The qualified forms count
# everywhere, the bare ones on the import's terms, like a literal.
_DYNAMIC = re.compile(
    r'\b(?:Trace|TraceCompat)\s*\.\s*(?:beginSection|beginAsyncSection)'
    r'\s*\(\s*[A-Za-z_$]'
)
_DYNAMIC_BARE = re.compile(r'\btrace(?:Async)?\s*\(\s*[A-Za-z_$]')

# A logger's receiver. SLF4J's and log4j's `log.trace(message)` is a log line
# whatever the file imports, while `traces.trace<Items>(Marks.LOAD)` is a
# wrapper of the project's own and names a slice.
_LOGGER = re.compile(r'(?:m|_)?log(?:ger)?', re.IGNORECASE)
_RECEIVER = re.compile(r'([A-Za-z_]\w*)\s*\.\s*$')


def _logged(before: str) -> bool:
    """Whether the call that starts right after `before` is made on a logger."""
    m = _RECEIVER.search(before)
    return bool(m and _LOGGER.fullmatch(m.group(1)))


# Kotlin's string template: `$name` or `${…}` not escaped as `\$`. Such a
# literal is a name built at runtime, and written into the map as it stands it
# never matches the name the trace carries.
_TEMPLATE = re.compile(r'\\.|\$(?:\{|[A-Za-z_])')
_ESCAPE = re.compile(r'\\(u[0-9a-fA-F]{4}|.)')
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "s": " "}


def _kotlin(path: Path) -> bool:
    return path.suffix in (".kt", ".kts")


def _templated(raw: str) -> bool:
    return any(m.group().startswith("$") for m in _TEMPLATE.finditer(raw))


def _unescape(raw: str) -> str:
    """A literal's source text → the string it holds: `\\$5` is `$5`, `it\\'s` is `it's`."""
    def one(m: re.Match) -> str:
        c = m.group(1)
        return chr(int(c[1:], 16)) if len(c) == 5 else _ESCAPES.get(c, c)
    return _ESCAPE.sub(one, raw)

# A name held in a constant. On a real project every marker was written this
# way: `object Marks { const val LOAD = "collection_load" }`,
# and the calls read `AppTraces.start(LOAD)` through a wrapper
# of the project's own. Not one of them was a literal at its call, and the
# map came back empty over the whole tree — "no instrumentation", to an
# agent that then asked the human how to make the markers visible.
#
# `const val` and Java's `static final String` only. A plain `val` is a
# literal just as often, but a local one shares its name with every other
# local in the project, and a map keyed on the simple name would resolve
# them into each other. The two forms below are the ones a project uses for
# a name it means to share, which is what a marker's name is.
_CONST = re.compile(
    r'\bconst\s+val\s+([A-Za-z_]\w*)\s*(?::\s*String\s*)?=\s*"((?:[^"\\]|\\.)*)"'
)
_JAVA_CONST = re.compile(
    r'\b(?:static\s+final|final\s+static)\s+String\s+([A-Za-z_]\w*)\s*=\s*"((?:[^"\\]|\\.)*)"'
)
# A call whose callee says "trace", with an identifier for a name:
# `AppTraces.start(APP_INIT)`,
# `Traces.createTrace(Marks.LOAD)`, `Trace.beginSection(TAG)`.
# The callee is the filter, not the argument: `TimeProfiler.start(Marks.X)`
# passes the same constant to something that writes no slice.
#
# Measured on a real project, and every exclusion below is a row that came
# back the first time. "section" alone let in a menu's
# sections — `clickOnSection(TAB)`, `findValueByKey(KEY)` from a
# `sectionItem` — so it counts only as `beginSection` and its kin. A callee
# that puts, sets or gets is handing the trace an attribute or a counter, not
# a name: `trace.putAttribute(Attr.REGION, …)` mapped a slice
# called `region`. And `TraceSectionMetric(Marks.X)` is a benchmark
# reading the marker, not the app writing it; the word "metric" is the tell.
# The optional `<…>` is a type argument: `traces.trace<State>(REFRESH_STATE)`
# is how a wrapper that returns what the block returns gets called.
_NAMED_CALL = re.compile(
    r'\b([A-Za-z_][\w.]*)\s*(?:<[^()]*>)?\s*\(\s*([A-Za-z_][\w.]*)\s*[,)]')
_TRACING_CALLEE = re.compile(
    r'trace|(?:begin|start|end|with|async)\w*section', re.IGNORECASE)
_NOT_A_NAME = re.compile(
    r'metric|attribute|counter|(?:^|\.)(?:put|set|get)\w*$', re.IGNORECASE)

# Where a call cannot be the app's: tests read markers or fake them, and a
# map that points a finding at `src/test` sends the reader to a file the
# device never ran. The lines are still counted as source — they are — and
# the calls in them are not sites.
_TEST_SETS = {"test", "androidTest", "testFixtures"}


def _test_source(path: Path) -> bool:
    parts = path.parts
    return any(parts[i] == "src" and parts[i + 1] in _TEST_SETS
               for i in range(len(parts) - 1))


# A call that closes a section names the one a begin already named:
# `Trace.endAsyncSection(Marks.LOAD, 7)`, a wrapper's `AppTraces.stop(LOAD)`.
# Counted, every begin and end pair was two sites, and the hint could land on
# the end.
_CLOSES = re.compile(r'(?:end|stop|finish)', re.IGNORECASE)


def _names_a_slice(callee: str) -> bool:
    parts = callee.split(".")
    if _CLOSES.match(parts[-1]):
        return False
    if len(parts) > 1 and _LOGGER.fullmatch(parts[-2]):
        return False
    return bool(_TRACING_CALLEE.search(callee)) and not _NOT_A_NAME.search(callee)

_SYMBOL = re.compile(
    r'^\s*(?:@[\w.]+(?:\([^)]*\))?\s+)*'
    r'(?:(?:public|private|internal|protected|suspend|inline|override|open|'
    r'final|static|abstract|operator|infix|tailrec|external|data|sealed|enum|'
    r'annotation|inner|value|companion|const|lateinit|actual|expect)\s+)*'
    r'(fun|class|object|interface|val|var)\s+(?:<[^>]*>\s*)?(?:[\w?<>, ]+\.)?'
    r'([A-Za-z_]\w*)'
)
# A Java method: modifiers, return type, name, parentheses, opening brace.
# Without it the hint points at the class while the call sits inside a method.
_JAVA_METHOD = re.compile(
    r'^\s*(?:@\w+\s+)*(?:(?:public|private|protected|static|final|'
    r'synchronized|abstract|native)\s+)+[\w<>\[\],.\s]+?\s+(\w+)\s*'
    r'\([^;{]*\)\s*(?:throws [\w,.\s]+)?\{'
)


@dataclass
class Site:
    name: str
    path: Path
    line: int
    module: str
    symbol: str | None
    # The identifier the call passed, as written, when the name reached the
    # call through a constant rather than as a literal. What a reader greps
    # for at that line, since the literal is not there.
    via: str | None = None


@dataclass
class ModuleStat:
    module: str
    files: int = 0
    lines: int = 0
    sites: int = 0
    dynamic: int = 0


def gradle_module(path: Path, root: Path) -> str:
    """Nearest ancestor holding a build script → `:path:module`."""
    current = path.parent
    while True:
        if (current / "build.gradle.kts").exists() or \
           (current / "build.gradle").exists():
            rel = current.relative_to(root)
            return ":" + ":".join(rel.parts) if rel.parts else ":"
        if current == root or current.parent == current:
            return ":"
        current = current.parent


def _indent(line: str) -> int:
    line = line.expandtabs(4)
    return len(line) - len(line.lstrip())


def _declared(line: str) -> tuple[str, str] | None:
    m = _SYMBOL.match(line)
    if m:
        return m.group(1), m.group(2)
    m = _JAVA_METHOD.match(line)
    return ("method", m.group(1)) if m else None


def _symbol_at(lines: list[str], index: int) -> str | None:
    """The declaration the call sits in — enough to make the hint useful.

    The call's own line, or the nearest declaration above it indented less:
    one at the call's depth is a neighbouring statement, and `val items = …`
    above `Trace.beginSection(…)` named the hint instead of the function. A
    `val` or `var` that holds the call names it only at class or file level,
    a property its initializer builds; a local one gives way to the function
    around it.
    """
    held = None
    depth = None
    for i in range(index, max(-1, index - 60), -1):
        line = lines[i]
        if depth is not None and (not line.strip() or _indent(line) >= depth):
            continue
        if depth is None:
            depth = _indent(line)
        found = _declared(line)
        if not found:
            continue
        kind, name = found
        if kind in ("val", "var"):
            held = held or f"{kind} {name}"
            depth = _indent(line)
            continue
        if kind in ("fun", "method") or held is None:
            return f"{kind} {name}"
        return held
    return held


def constants(texts: dict[Path, str]) -> dict[str, str | None]:
    """Simple name → the string it holds, across the whole project.

    Keyed on the simple name because that is what a call site shows:
    `AppTraces.start(APP_INIT)` after an import, or
    `Marks.APP_INIT` qualified, and either way the owner is not
    worth resolving for a map whose reader will grep the name anyway. Two
    constants of one name holding different strings resolve to neither —
    None, kept in the map so a later same-name declaration cannot quietly
    win — because a guess between them would send the reader to the wrong
    file with a confident hint. A Kotlin constant built from a template,
    `"${PREFIX}_load"`, resolves to None as well: its value is not the text.
    """
    out: dict[str, str | None] = {}
    for path, text in texts.items():
        kotlin = _kotlin(path)
        for pattern in (_CONST, _JAVA_CONST):
            for m in pattern.finditer(text):
                name, raw = m.group(1), m.group(2)
                value = None if kotlin and _templated(raw) else _unescape(raw)
                if name in out and out[name] != value:
                    out[name] = None
                elif name not in out:
                    out[name] = value
    return out


def scan(root: Path) -> tuple[list[Site], dict[str, ModuleStat]]:
    sites: list[Site] = []
    stats: dict[str, ModuleStat] = {}

    # Read everything first: a call resolves through a constant declared in
    # another module, and the walk is in path order, which is not dependency
    # order.
    texts: dict[Path, str] = {}
    for path in source_files(root):
        try:
            texts[path] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    names = constants(texts)

    for path, text in texts.items():
        module = gradle_module(path, root)
        stat = stats.setdefault(module, ModuleStat(module))
        lines = text.splitlines()
        stat.files += 1
        stat.lines += len(lines)

        if _test_source(path):
            continue
        bare_ok = bool(_BARE_IMPORT.search(text))
        kotlin = _kotlin(path)
        for index, line in enumerate(lines):
            literals = [m.group(1) for m in _QUALIFIED.finditer(line)]
            if bare_ok:
                literals += [m.group(1) for m in _BARE.finditer(line)
                             if not _logged(line[:m.start()])]
            built = sum(1 for raw in literals if kotlin and _templated(raw))
            found: list[tuple[str, str | None]] = \
                [(_unescape(raw), None) for raw in literals
                 if not (kotlin and _templated(raw))]
            for m in _NAMED_CALL.finditer(line):
                if not _names_a_slice(m.group(1)):
                    continue
                literal = names.get(m.group(2).rsplit(".", 1)[-1])
                if literal is not None:
                    found.append((literal, m.group(2)))
            for name, via in found:
                sites.append(Site(name, path, index + 1, module,
                                  _symbol_at(lines, index), via))
                stat.sites += 1
            # A name that resolved is not one built at runtime, whatever
            # the call looks like: `Trace.beginSection(TAG)` with TAG a
            # constant is a literal at one remove, not a gap.
            dynamic = bool(_DYNAMIC.search(line)) or bare_ok and any(
                not _logged(line[:m.start()]) for m in _DYNAMIC_BARE.finditer(line))
            stat.dynamic += built + (dynamic and not any(via for _, via in found))

    return sites, stats


_GLOB_SPECIAL = re.compile(r"([\[*?])")


def _quoted(value: str) -> str:
    """A double-quoted YAML scalar: JSON's escapes are YAML's."""
    return json.dumps(value, ensure_ascii=False)


def render(sites: list[Site], stats: dict[str, ModuleStat],
           root: Path, limit: int = 12) -> list[str]:
    """A ready-to-paste domains section plus a coverage report."""
    out: list[str] = []
    total_lines = sum(s.lines for s in stats.values())
    total_sites = sum(s.sites for s in stats.values())
    total_dynamic = sum(s.dynamic for s in stats.values())

    out.append(f"# Instrumentation: {total_sites} tracing calls "
               f"across {total_lines} lines of source.")
    via = sum(1 for s in sites if s.via)
    if via:
        out.append(f"# {via} of them name the slice through a constant — "
                   f"mapped to the call, not to the declaration.")
    if total_dynamic:
        out.append(f"# Another {total_dynamic} calls build the name at runtime "
                   f"— visible in the trace, absent from this map.")

    if not sites:
        out.append("#")
        out.append("# No instrumentation. There is nothing to attach findings")
        out.append("# to: the detectors will show system slices and blind")
        out.append("# spots, but not a place in the code. `echolot mark`")
        out.append("# names the entry points the first markers go to. Modules")
        out.append("# with the most code and no instrumentation at all:")
        out.append("#")
        empty = sorted((s for s in stats.values() if not s.sites),
                       key=lambda s: -s.lines)[:limit]
        for stat in empty:
            out.append(f"#   {stat.module:<28} {stat.lines:>7} lines, "
                       f"{stat.files} files")
        out.append("#")
        out.append("# domains: []")
        return out

    by_name: dict[str, list[Site]] = {}
    for site in sites:
        by_name.setdefault(site.name, []).append(site)

    out.append("#")
    out.append("# A pre-filled map. The hint field is for humans: fix the")
    out.append("# wording, the engine never reads it.")
    out.append("domains:")
    for name in sorted(by_name):
        found = by_name[name]
        first = found[0]
        rel = first.path.relative_to(root)
        hint = f"{rel.name}:{first.line}"
        if first.symbol:
            hint += f" — {first.symbol}"
        if first.via:
            # The literal is not on that line; this is what is.
            hint += f", via {first.via}"
        # Quoted by a writer that knows YAML: Kotlin's `\$` copied into a
        # double-quoted scalar broke the whole echolot.yml. And `analyze`
        # reads the entry as a GLOB, so a `[`, `*` or `?` is escaped to match
        # itself.
        glob = _GLOB_SPECIAL.sub(r"[\1]", name)
        out.append(f"  - slice: {_quoted(glob)}")
        if glob != name:
            out.append("    # a GLOB: [, * and ? are escaped to match themselves")
        out.append(f"    module: {_quoted(first.module)}")
        out.append(f"    hint: {_quoted(hint)}")
        if len(found) > 1:
            others = ", ".join(
                f"{s.path.relative_to(root)}:{s.line}" for s in found[1:4])
            out.append(f"    # {len(found) - 1} more: {others}")
    return out
