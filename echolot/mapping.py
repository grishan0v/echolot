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
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

# A class: `com.example.app.Store -> a.b:`. Its members follow, indented.
_CLASS = re.compile(r"^(?P<real>[^\s#]\S*)\s+->\s+(?P<min>[^\s:]+):\s*$")
# A method: `    1:4:void save(java.lang.String):20:23 -> c`. The line ranges
# before and after are R8's, for inlining and for stack traces with lines. A
# sample has no line, so only the names are kept. A name with a class in it is
# a method inlined from that class, and trace_processor takes it as it is.
_METHOD = re.compile(
    r"^\s+(?:\d+:\d+:)?\S+\s+(?P<real>[^\s(]+)\([^)]*\)(?::\d+(?::\d+)?)?"
    r"\s+->\s+(?P<min>\S+)\s*$")
# What R8 makes of a class it renames: one to three lower-case letters and
# digits, and the same after each `$` of a nested class. A class a person named
# starts with a capital, in Kotlin and in Java.
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
