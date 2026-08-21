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
    found: list[Path] = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        here = Path(base)
        if under_src and "src" not in here.parts:
            continue
        found += [here / n for n in names if n.endswith(SOURCE_EXT)]
    return sorted(found)

# Qualified forms are unambiguous and count everywhere.
_QUALIFIED = re.compile(
    r'\b(?:Trace|TraceCompat)\s*\.\s*'
    r'(?:beginSection|beginAsyncSection)\s*\(\s*"((?:[^"\\]|\\.)*)"'
)
# Bare trace("...") counts only where androidx.tracing is imported; otherwise
# any logging function with that name would land in the map.
_BARE = re.compile(r'\btrace\s*\(\s*"((?:[^"\\]|\\.)*)"')
_BARE_IMPORT = re.compile(r'^\s*import\s+androidx\.tracing', re.MULTILINE)

# A call with a non-literal argument: the name is assembled at runtime and
# cannot be recovered statically. Worth counting — such sites are visible in
# the trace but will never appear in the map.
_DYNAMIC = re.compile(
    r'\b(?:trace|Trace\s*\.\s*beginSection|TraceCompat\s*\.\s*beginSection)'
    r'\s*\(\s*(?!")[A-Za-z_$]'
)

_SYMBOL = re.compile(
    r'^\s*(?:@\w+\s+)*(?:(?:public|private|internal|protected|suspend|'
    r'inline|override|open|final|static|abstract)\s+)*'
    r'(fun|class|object|interface|val|var)\s+([A-Za-z_]\w*)'
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


def _symbol_at(lines: list[str], index: int) -> str | None:
    """The nearest declaration above — enough to make the hint useful."""
    for i in range(index, max(-1, index - 60), -1):
        m = _JAVA_METHOD.match(lines[i])
        if m:
            return f"method {m.group(1)}"
        m = _SYMBOL.match(lines[i])
        if m:
            return f"{m.group(1)} {m.group(2)}"
    return None


def scan(root: Path) -> tuple[list[Site], dict[str, ModuleStat]]:
    sites: list[Site] = []
    stats: dict[str, ModuleStat] = {}

    for path in source_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        module = gradle_module(path, root)
        stat = stats.setdefault(module, ModuleStat(module))
        lines = text.splitlines()
        stat.files += 1
        stat.lines += len(lines)

        bare_ok = bool(_BARE_IMPORT.search(text))
        for index, line in enumerate(lines):
            found = [m.group(1) for m in _QUALIFIED.finditer(line)]
            if bare_ok:
                found += [m.group(1) for m in _BARE.finditer(line)]
            for name in found:
                sites.append(Site(name, path, index + 1, module,
                                  _symbol_at(lines, index)))
                stat.sites += 1
            if _DYNAMIC.search(line):
                stat.dynamic += 1

    return sites, stats


def render(sites: list[Site], stats: dict[str, ModuleStat],
           root: Path, limit: int = 12) -> list[str]:
    """A ready-to-paste domains section plus a coverage report."""
    out: list[str] = []
    total_lines = sum(s.lines for s in stats.values())
    total_sites = sum(s.sites for s in stats.values())
    total_dynamic = sum(s.dynamic for s in stats.values())

    out.append(f"# Instrumentation: {total_sites} tracing calls "
               f"across {total_lines} lines of source.")
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
        out.append(f'  - slice: "{name}"')
        out.append(f'    module: "{first.module}"')
        out.append(f'    hint: "{hint}"')
        if len(found) > 1:
            others = ", ".join(
                f"{s.path.relative_to(root)}:{s.line}" for s in found[1:4])
            out.append(f"    # {len(found) - 1} more: {others}")
    return out
