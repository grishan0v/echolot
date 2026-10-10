"""The build's R8 mapping, for the frames callstack samples bring back.

A benchmark build is meant to run what the release runs, and that usually
means minified: the sampled frames of the app's own code, and of most of its
libraries, come back as `a.b.c`, and the frames a blind-spot row names
(stacks.py) are only worth reading with their real names. R8 writes how it
renamed everything into `mapping.txt`, and `project.mapping` names that file.

trace_processor turns the names back itself. A `DeobfuscationMapping` packet
in the trace fills `stack_profile_frame.deobfuscated_name`, so this module
reads the mapping into one such packet, and `analyze` hands it to
trace_processor after the trace; the file on disk is not touched. What the
pinned trace_processor does with the packet was checked, not taken from the
documentation:

- methods go in `obfuscated_methods`. `obfuscated_members` is for fields, and
  a method put there is never looked at;
- the packet names the package, and a frame is matched to it by its file's
  path on the device, `/data/app/~~…/com.example.app-…/base.apk`, or, for
  code the JIT compiled, by its process;
- a frame of a renamed class whose method the packet does not list keeps its
  minified name, so every method of a renamed class goes in;
- one minified name for several methods — R8 gives overloads one name —
  comes back as all of them, `com.example.app.Store.load |
  com.example.app.Store.save`;
- a method inlined from another class keeps that class's name.

A large app's mapping takes seconds to read. A synthetic one of 125 MB, with
60,000 classes, took 4.6 s to read and 0.9 s to pack, and a trace loaded
1.5 s slower with it. So it is read once per process, whatever the number of
traces.

The runtime also writes frames as text, and trace_processor touches none of
them: both sides of a lock in ART's contention slice, and every thread of an
ANR record. Those carry a line, and a line is what R8's own `retrace` goes
by: `retracer` reads the line ranges of the classes such frames name, and
`Retracer.retrace` turns a frame back into the method and line it came from,
out through the methods inlined there. What R8 9.0 writes, and what a Galaxy
A51 on Android 13 printed for a test app built three ways:

- with `-keepattributes SourceFile,LineNumberTable` and
  `-renamesourcefileattribute SourceFile`, the lock slice and the ANR record
  both say `a.a.run(SourceFile:6)`, a line R8 numbered itself;
- with no `-keepattributes`, both say `a.a.run(r8-map-id-…:6)`: R8 keeps a
  line table anyway and names the file after the mapping;
- with line numbers kept and a minimum API of 26 or more, R8 drops the line
  table and maps the offsets of the instructions instead. The ANR record
  prints the offset where the line would be, `a.a.run(unavailable:20)`, and
  that retraces exactly. The lock slice prints `-1`, and is named by its
  method alone.

Every one of those frames comes back here as R8's own `retrace` names it.
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# A class: `com.example.app.Store -> a.b:`. Its members follow, indented.
_CLASS = re.compile(r"^(?P<real>[^\s#]\S*)\s+->\s+(?P<min>[^\s:]+):\s*$")
# A method: `    1:4:void save(java.lang.String):20:23 -> c`. The ranges are
# R8's: the lines of the method it kept first, then the lines they came from.
# A sample has no line, so the packet keeps only the names. A name with a class
# in it is a method inlined from that class, and trace_processor takes it as it
# is.
_METHOD = re.compile(
    r"^\s+(?:(?P<first>\d+):(?P<last>\d+):)?\S+\s+(?P<real>[^\s(]+)\([^)]*\)"
    r"(?::(?P<start>\d+)(?::(?P<end>\d+))?)?\s+->\s+(?P<min>\S+)\s*$")
# What a frame's parenthesis ends in: a line, `-1` when the runtime had none,
# or, for a build R8 left without a line table, an instruction's offset.
_NUMBER = re.compile(r"-?\d+")
# What R8 makes of a class it renames: one to three lower-case letters and
# digits. After each `$` of a nested class, one to three letters or digits of
# either case: the outer name already says the class was renamed, so `a$Tab`
# reads as minified too. A class a person named starts with a capital, in
# Kotlin and in Java.
_MINIFIED = re.compile(r"[a-z][a-z0-9]{0,2}(?:\$[A-Za-z0-9]{1,3})*")

# Minified class name → (real class name, minified method name → real names).
Classes = dict[str, tuple[str, dict[str, set[str]]]]


def read(path: Path) -> Classes:
    """The classes and methods a mapping renamed, by their minified names.

    A class R8 renamed keeps every method, renamed or not: its frames are
    matched by class and method together, and a method left out would keep
    the class's minified name. A class it did not rename keeps only the
    methods it did. Fields are not read, since a frame is never one.
    """
    found: Classes = {}
    current: dict[str, set[str]] | None = None
    with path.open(encoding="utf-8", errors="replace") as text:
        for line in text:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if not line[0].isspace():
                m = _CLASS.match(line)
                current = None
                if m:
                    current = found.setdefault(m.group("min"), (m.group("real"), {}))[1]
                continue
            if current is None or "(" not in line:
                continue
            m = _METHOD.match(line)
            if m:
                current.setdefault(m.group("min"), set()).add(m.group("real"))
    kept: Classes = {}
    for minified, (real, methods) in found.items():
        if real == minified:
            methods = {name: reals for name, reals in methods.items() if reals != {name}}
        if methods:
            kept[minified] = (real, methods)
    return kept


def packet(package: str, classes: Classes) -> bytes:
    """One trace packet that names the classes back, for `package`'s frames.

    perfetto's protobuf classes are imported here rather than at the top: they
    bring protobuf with them, and every command imports this module through
    `main` while only `analyze` with a mapping needs them.
    """
    from perfetto.protos.perfetto.trace import perfetto_trace_pb2 as pb

    trace = pb.Trace()
    mapping = trace.packet.add().deobfuscation_mapping
    mapping.package_name = package
    for minified in sorted(classes):
        real, methods = classes[minified]
        entry = mapping.obfuscated_classes.add()
        entry.obfuscated_name, entry.deobfuscated_name = minified, real
        for name in sorted(methods):
            for real_name in sorted(methods[name]):
                method = entry.obfuscated_methods.add()
                method.obfuscated_name, method.deobfuscated_name = name, real_name
    return trace.SerializeToString()


def packet_for(path: Path, package: str) -> bytes:
    """The packet for a mapping file, read once while the file stays the same."""
    stat = path.stat()
    return _packet_for(path.resolve(), stat.st_mtime_ns, stat.st_size, package)


@functools.lru_cache(maxsize=2)
def _packet_for(path: Path, mtime_ns: int, size: int, package: str) -> bytes:
    return packet(package, read(path))


def minified(method: str) -> bool:
    """Whether a method's class has the shape R8 gives a class it renamed."""
    parts = method.split(".")
    return len(parts) >= 2 and _MINIFIED.fullmatch(parts[-2]) is not None


# --- frames written as text -------------------------------------------------


@dataclass(frozen=True)
class Range:
    """One method line of a class: which of its lines came from where.

    `first`–`last` are lines of the method R8 kept, `start`–`end` the lines
    of the source they came from, and either side can be missing. `owner` is
    the class of a method inlined from another class.
    """
    name: str
    owner: str | None = None
    first: int | None = None
    last: int | None = None
    start: int | None = None
    end: int | None = None

    def holds(self, line: int) -> bool:
        return self.first is not None and self.last is not None \
            and self.first <= line <= self.last

    def original(self, line: int | None) -> int | None:
        """The source line a line of R8's came from, read as R8's `retrace` reads it.

        Without an original range the line did not move. One original line,
        or one line of R8's, is that line: the call site of a method inlined
        there. Otherwise the two ranges run side by side. An original line of
        0 is R8's way of writing that there was none.
        """
        if line is None:
            return None
        if self.start is None:
            return line
        if self.start == 0:
            return None
        if self.end is None or self.end == self.start \
                or self.first is None or self.first == self.last:
            return self.start
        return min(self.start + line - self.first, self.end)


@dataclass(frozen=True)
class Frame:
    """One frame as the source names it."""
    cls: str
    method: str
    file: str
    line: int | None

    @property
    def symbol(self) -> str:
        return f"{self.cls}.{self.method}"

    def text(self) -> str:
        """As a stack trace prints it: `pkg.Class.method(File.kt:12)`."""
        if self.line:
            return f"{self.symbol}({self.file}:{self.line})"
        return f"{self.symbol}({self.file})"


@dataclass(frozen=True)
class Retraced:
    """What the mapping makes of one frame R8 wrote.

    `chains` are what the frame can be, each innermost first: the method
    inlined at its line, then the one that was inlined into, out to the
    method R8 kept. A line picks one. A frame without a line is named by the
    method R8 kept, and is several only where R8 gave several methods one
    name.

    `outside` is a frame the mapping has no place for: its line in no range
    of its method, or its method not in its class at all. The mapping of the
    build that wrote the frame never leaves one, and another build's leaves
    many. Such a frame gets its class back and keeps R8's method name, as
    R8's `retrace` writes it, and no line: R8's line of a method nobody can
    name points nowhere.
    """
    chains: tuple[tuple[Frame, ...], ...]
    outside: bool = False

    @property
    def frames(self) -> tuple[Frame, ...]:
        """The frames of the first chain, innermost first."""
        return self.chains[0]


@dataclass
class _Named:
    """A class the mapping lists: its real name, and its methods by R8's names."""
    real: str
    methods: dict[str, list[Range]] = field(default_factory=dict)


def where(text: str) -> tuple[str | None, int | None]:
    """The file and the number in a frame's parenthesis.

    `SourceFile:6` is a file and a line; `Store.kt:-1` a file and no line;
    `unavailable:20` is ART's way of printing an instruction's offset where
    there is no line table, and the offset is what such a build's mapping is
    written in. `Native method` is a file of sorts and no number.
    """
    text = text.strip()
    file, colon, number = text.rpartition(":")
    if colon and _NUMBER.fullmatch(number):
        n = int(number)
        return file or None, n if n >= 0 else None
    return text or None, None


class Retracer:
    """The classes some frames name, read out of a mapping with their lines."""

    def __init__(self, classes: dict[str, _Named], files: dict[str, str]):
        self._classes = classes
        # Every class's file by its real name: a method inlined from a class
        # names that class, and only its own entry says where it was written.
        self._files = files

    def real(self, cls: str) -> str | None:
        """The source's name for a class R8 renamed, None for one it kept."""
        named = self._classes.get(cls)
        return named.real if named is not None and named.real != cls else None

    def retrace(self, cls: str, method: str, file: str | None,
                line: int | None) -> Retraced | None:
        """The frame R8 wrote as `cls.method(file:line)`, by its source's names.

        None when the frame is not one of R8's: its class is not in the
        mapping, or the class kept its name and the frame already reads as
        the source does (see `_ours`).
        """
        named = self._classes.get(cls)
        if named is None:
            return None
        chains = _chains(named.methods.get(method, []))
        if named.real == cls and not _ours(chains, method, file, line):
            return None
        if line is not None:
            held = [c for c in chains if c[0].holds(line)] \
                or [c for c in chains if c[0].first is None]
            if held:
                return Retraced(tuple(tuple(self._frame(named, r, line) for r in chain)
                                      for chain in held))
        elif chains:
            outer: list[Frame] = []
            for chain in chains:
                frame = self._frame(named, chain[-1], None)
                if frame not in outer:
                    outer.append(frame)
            return Retraced(tuple((frame,) for frame in outer))
        return Retraced(((Frame(named.real, method, self._file(named.real), None),),),
                        outside=True)

    def _frame(self, named: _Named, r: Range, line: int | None) -> Frame:
        cls = r.owner or named.real
        return Frame(cls, r.name, self._file(cls), r.original(line))

    def _file(self, cls: str) -> str:
        """The file a class was written in: the mapping's word, else R8's guess.

        A nested class lives in its outer class's file. With neither in the
        mapping, R8's `retrace` guesses the outer class's name in Java, and
        so does this.
        """
        outer = cls.split("$", 1)[0]
        return self._files.get(cls) or self._files.get(outer) \
            or outer.rsplit(".", 1)[-1] + ".java"


def _chains(ranges: list[Range]) -> list[list[Range]]:
    """One method name's lines, grouped by what R8 inlined where.

    R8 writes the methods inlined at one stretch of lines on consecutive
    lines with the same range, innermost first, and the method it kept last.
    A line without a range stands alone.
    """
    out: list[list[Range]] = []
    for r in ranges:
        if out and r.first is not None \
                and (out[-1][-1].first, out[-1][-1].last) == (r.first, r.last):
            out[-1].append(r)
        else:
            out.append([r])
    return out


def _ours(chains: list[list[Range]], method: str, file: str | None,
          line: int | None) -> bool:
    """Whether a frame of a class R8 kept is one R8 wrote.

    Such a class is in the mapping under its own name, and so is a frame of
    it that already reads as the source: an export whose console retraced it.
    R8's frame is the one whose method R8 renamed, or that names no source
    file — `SourceFile`, `r8-map-id-…`, `unavailable` — or whose line is one
    of R8's. A method the mapping does not list is the class's own.
    """
    if not chains:
        return False
    if any(chain[-1].name != method or chain[-1].owner for chain in chains):
        return True
    if not (file or "").endswith((".kt", ".java")):
        return True
    return line is not None and any(chain[0].holds(line) for chain in chains)


def retracer(path: Path, classes: Iterable[str]) -> Retracer:
    """A retracer for frames of these classes, read once while the file stays the same."""
    stat = path.stat()
    return _retracer(path.resolve(), stat.st_mtime_ns, stat.st_size, frozenset(classes))


@functools.lru_cache(maxsize=4)
def _retracer(path: Path, mtime_ns: int, size: int, wanted: frozenset[str]) -> Retracer:
    return Retracer(*_read_lines(path, wanted))


def _read_lines(path: Path, wanted: frozenset[str]) -> tuple[dict[str, _Named], dict[str, str]]:
    """In one pass: the method lines of the classes wanted, and every class's file.

    Only the classes some frame names are kept with their lines: a frame is
    looked up by its own class, and a large app's mapping holds millions of
    lines nobody asked about. A synthetic one of 133 MB, read for three
    classes, took 0.6 s, where reading it for the packet took 5.8 s. A class
    R8 kept is read like one it renamed, since its lines are R8's either way.
    """
    found: dict[str, _Named] = {}
    files: dict[str, str] = {}
    real: str | None = None
    current: _Named | None = None
    with path.open(encoding="utf-8", errors="replace") as text:
        for line in text:
            head = line[:1]
            if head == "#":
                # The class's file is on a comment of its own under it:
                # `# {"id":"sourceFile","fileName":"Store.kt"}`.
                if real is not None and "sourceFile" in line:
                    name = _source_file(line)
                    if name:
                        files[real] = name
                continue
            if head.isspace():
                if current is None or "(" not in line or line.lstrip().startswith("#"):
                    continue
                m = _METHOD.match(line)
                if m:
                    current.methods.setdefault(m.group("min"), []).append(_range(m))
                continue
            m = _CLASS.match(line)
            real = current = None
            if m:
                real = m.group("real")
                if m.group("min") in wanted:
                    current = found.setdefault(m.group("min"), _Named(real))
    return found, files


def _range(m: re.Match[str]) -> Range:
    owner, _, name = m.group("real").rpartition(".")

    def number(group: str) -> int | None:
        value = m.group(group)
        return int(value) if value is not None else None

    return Range(name, owner or None, number("first"), number("last"),
                 number("start"), number("end"))


def _source_file(line: str) -> str | None:
    try:
        meta = json.loads(line.lstrip("#").strip())
    except ValueError:
        return None
    if isinstance(meta, dict) and meta.get("id") == "sourceFile":
        name = meta.get("fileName")
        return name if isinstance(name, str) and name else None
    return None
