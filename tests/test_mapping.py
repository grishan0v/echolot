#!/usr/bin/env python3
"""The build's R8 mapping — `echolot/mapping.py`, and what the config and the report make of it.

What trace_processor does with the packet is the self-check's to pin, in
`R8 mapping: a minified build's frames come back by their names`. Here: what
is read out of a mapping.txt, what goes into the packet, the words, and how
a frame the runtime wrote as text — a lock slice's, an ANR record's — is
retraced, against R8's own output and what a phone printed (lockapp.py).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from perfetto.protos.perfetto.trace import perfetto_trace_pb2 as pb  # noqa: E402

from echolot import fixture, mapping  # noqa: E402
from echolot import report as report_mod  # noqa: E402
from echolot.config import Config, ConfigError  # noqa: E402
from echolot.tp import _then  # noqa: E402
from tests import lockapp  # noqa: E402
from tests.support import check  # noqa: E402

# R8's output for a few shapes it writes: a renamed class with line ranges,
# a method inlined from another class, a field, comments, a class it kept
# whose method it renamed anyway, and one it left alone altogether.
R8 = """\
# compiler: R8
# compiler_version: 8.5.35
# pg_map_id: 3f9a2c1
com.example.app.Store -> a.b:
# {"id":"sourceFile","fileName":"Store.kt"}
    1:1:void <init>():12:12 -> <init>
    1:6:void save(java.lang.String):22:27 -> c
    7:9:void save(java.lang.String):30:32 -> c
    1:4:void com.example.app.Inner.inlined():10:13 -> d
    1:4:void flush():40 -> d
    java.lang.String path -> f
com.example.app.Store$Companion -> a.b$a:
    1:2:com.example.app.Store create(android.content.Context):80:81 -> a
com.example.app.KeptActivity -> com.example.app.KeptActivity:
    1:3:void onCreate(android.os.Bundle):15:17 -> onCreate
    1:2:void helper():40:41 -> a
com.example.app.Untouched -> com.example.app.Untouched:
    1:2:void run():5:6 -> run
"""


def test_a_mapping_is_read_down_to_the_methods_a_frame_can_be(tmp_path: Path) -> None:
    path = tmp_path / "mapping.txt"
    path.write_text(R8, encoding="utf-8")
    classes = mapping.read(path)
    store = classes["a.b"]
    check("a renamed class, by its minified name", store[0] == "com.example.app.Store", store)
    check("every method of it, the constructor too, each name once over its line ranges",
          store[1] == {"<init>": {"<init>"}, "c": {"save"},
                       "d": {"com.example.app.Inner.inlined", "flush"}}, store[1])
    check("a nested class keeps its `$`",
          classes["a.b$a"] == ("com.example.app.Store$Companion", {"a": {"create"}}),
          classes.get("a.b$a"))
    check("a kept class only for the method it renamed",
          classes["com.example.app.KeptActivity"]
          == ("com.example.app.KeptActivity", {"a": {"helper"}}), classes)
    check("and a class R8 left alone not at all", "com.example.app.Untouched" not in classes,
          sorted(classes))


def test_the_packet_names_the_package_and_lists_methods_where_trace_processor_looks(
        tmp_path: Path) -> None:
    path = tmp_path / "mapping.txt"
    path.write_text(R8, encoding="utf-8")
    trace = pb.Trace.FromString(mapping.packet("com.example.app.beta", mapping.read(path)))
    found = trace.packet[0].deobfuscation_mapping
    check("one packet, for the package as installed",
          len(trace.packet) == 1 and found.package_name == "com.example.app.beta", found)
    store = next(c for c in found.obfuscated_classes if c.obfuscated_name == "a.b")
    check("methods in obfuscated_methods, which is where it reads them",
          [(m.obfuscated_name, m.deobfuscated_name) for m in store.obfuscated_methods]
          == [("<init>", "<init>"), ("c", "save"), ("d", "com.example.app.Inner.inlined"),
              ("d", "flush")] and not store.obfuscated_members, store)
    check("classes in order, so the same mapping packs the same bytes",
          [c.obfuscated_name for c in found.obfuscated_classes]
          == ["a.b", "a.b$a", "com.example.app.KeptActivity"], found.obfuscated_classes)


def test_a_mapping_is_read_again_once_it_changes(tmp_path: Path) -> None:
    path = tmp_path / "mapping.txt"
    path.write_text(fixture.MAPPING, encoding="utf-8")
    first = mapping.packet_for(path, "com.example.app")
    check("read once while the file stays", mapping.packet_for(path, "com.example.app") is first)
    path.write_text(fixture.OTHER_MAPPING, encoding="utf-8")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
    check("and again once it does", mapping.packet_for(path, "com.example.app") != first)


@pytest.mark.parametrize("method, minified", [
    ("a.b.c", True),
    ("com.example.app.a.b", True),
    ("c.b$a.run", True),
    ("x.a$Tab.run", True),      # the outer name is R8's, whatever the nested part
    ("x.a$ABC.run", True),
    ("a0.ab.c", True),
    ("com.example.app.Store.save", False),
    ("com.example.app.StoreKt.a", False),
    ("com.example.app.Store$save$1.invokeSuspend", False),
    ("kotlinx.coroutines.DispatchedTask.run", False),
])
def test_what_reads_as_minified(method: str, minified: bool) -> None:
    check(f"{method}", mapping.minified(method) is minified, mapping.minified(method))


# --- the config ---------------------------------------------------------------

def cfg(tmp_path: Path, **project) -> Config:
    raw = {"project": {"package": "com.example.app", **project}}
    return Config(raw, tmp_path / "echolot.yml")


def test_project_mapping_is_a_file_next_to_the_config(tmp_path: Path) -> None:
    out = tmp_path / "app/build/outputs/mapping/benchmark"
    out.mkdir(parents=True)
    (out / "mapping.txt").write_text(fixture.MAPPING, encoding="utf-8")
    check("no key, no mapping", cfg(tmp_path).mapping is None)
    check("a relative path is taken from the config's directory",
          cfg(tmp_path, mapping="app/build/outputs/mapping/benchmark/mapping.txt").mapping
          == out / "mapping.txt")


@pytest.mark.parametrize("project, said", [
    ({"mapping": ["mapping.txt"]}, "must be a path"),
    ({"mapping": "nowhere/mapping.txt"}, "no such file"),
])
def test_a_mapping_the_config_cannot_use_is_said(tmp_path: Path, project: dict, said: str) -> None:
    with pytest.raises(ConfigError, match=said):
        _ = cfg(tmp_path, **project).mapping


def test_a_mapping_needs_the_package_it_is_for(tmp_path: Path) -> None:
    (tmp_path / "mapping.txt").write_text(fixture.MAPPING, encoding="utf-8")
    config = Config({"project": {"process": "com.example.app*", "mapping": "mapping.txt"}},
                    tmp_path / "echolot.yml")
    with pytest.raises(ConfigError, match="needs project.package"):
        _ = config.mapping


def test_the_packet_follows_the_trace_byte_for_byte(tmp_path: Path) -> None:
    trace = tmp_path / "t.perfetto-trace"
    trace.write_bytes(b"trace bytes")
    check("the trace, then the packet", b"".join(_then(trace, b"+packet")) == b"trace bytes+packet")


# --- the report ---------------------------------------------------------------

def test_the_header_says_how_the_apps_methods_came_back() -> None:
    check("a build that kept its names and has no mapping: nothing",
          report_mod._names_lines({"methods": 8, "minified": 0}) == [])
    lines = report_mod._names_lines({"methods": 8, "minified": 8})
    check("a minified build without one: the warning, and the key that helps",
          len(lines) == 1 and lines[0].startswith("> ⚠️ 8 of the app's 8")
          and "`project.mapping`" in lines[0], lines)
    lines = report_mod._names_lines({"methods": 8, "minified": 0, "renamed": 8})
    check("a mapping that named them all: one plain line",
          lines == ["`project.mapping` named 8 of the app's 8 sampled methods back, "
                    "and none reads as minified."], lines)
    lines = report_mod._names_lines({"methods": 8, "minified": 7, "renamed": 1})
    check("one from another build: what it renamed, what it missed, and the doubt",
          len(lines) == 1 and "7 of the app's 8" in lines[0] and "renamed 1" in lines[0]
          and "another build" in lines[0], lines)


def test_code_that_arrived_minified_is_no_warning() -> None:
    # A build that kept its names, on a phone: 38 of 3,309 methods read as
    # minified, all of them from SDKs that ship that way.
    check("no mapping, and too few to be the build's: nothing",
          report_mod._names_lines({"methods": 3309, "minified": 38}) == [])
    lines = report_mod._names_lines({"methods": 3309, "minified": 38, "renamed": 3200})
    check("a mapping that named the rest says so, and says what it could not",
          len(lines) == 1 and not lines[0].startswith(">")
          and "38 still read as minified" in lines[0] and "SDKs" in lines[0], lines)
    check("and a tenth is where the warning starts",
          report_mod._names_lines({"methods": 100, "minified": 10})[0].startswith("> ⚠️"))


def test_repeats_keep_the_most_minified_methods_any_of_them_saw() -> None:
    def one(minified: int) -> dict:
        return {"hz": 100, "started": True, "samples": 30, "with_stack": 28,
                "names": {"methods": 8, "minified": minified, "renamed": 8 - minified}}
    merged = report_mod._merge_sampling([one(0), one(3), one(0)])
    # The whole block from that repeat: its own `renamed` beside its own
    # `minified`, never one key's maximum from one repeat and the next key's
    # from another.
    check("the worst repeat speaks for the names", merged["names"]
          == {"methods": 8, "minified": 3, "renamed": 5}, merged)


# --- frames written as text ---------------------------------------------------
#
# Every name below is the one R8's own `retrace` (R8 9.0) printed for the same
# frame against the same mapping, except where a test says why it differs.

RETRACE = R8 + """\
com.example.app.Disk -> a.f:
# {"id":"sourceFile","fileName":"Disk.kt"}
    1:3:long checksum(java.io.File):12:14 -> c
    4:4:long checksumLegacy(java.io.File):30:30 -> c
    1:2:byte[] readSync(java.io.File):20:21 -> d
com.example.app.Plain -> a.g:
    void noLines() -> a
    1:3:void identity() -> b
"""


def retraced(mapping_text: str, frame: str, tmp_path: Path) -> mapping.Retraced | None:
    """One frame as a stack trace prints it, `pkg.Class.method(File:line)`, retraced."""
    path = tmp_path / "mapping.txt"
    path.write_text(mapping_text, encoding="utf-8")
    symbol, _, rest = frame.partition("(")
    cls, _, method = symbol.rpartition(".")
    file, line = mapping.where(rest.rstrip(")"))
    return mapping.retracer(path, {cls}).retrace(cls, method, file, line)


@pytest.mark.parametrize("frame, named", [
    # By the line: the two ranges run side by side.
    ("a.b.c(SourceFile:3)", ["com.example.app.Store.save(Store.kt:24)"]),
    ("a.b.c(SourceFile:8)", ["com.example.app.Store.save(Store.kt:31)"]),
    # A method inlined there comes first, then the one R8 kept, at the call.
    ("a.b.d(SourceFile:2)", ["com.example.app.Inner.inlined(Inner.java:11)",
                             "com.example.app.Store.flush(Store.kt:40)"]),
    # No line: the method R8 kept, whatever was inlined into it.
    ("a.b.c(SourceFile)", ["com.example.app.Store.save(Store.kt)"]),
    ("a.b.d(SourceFile)", ["com.example.app.Store.flush(Store.kt)"]),
    # A class R8 kept: a method it renamed, one it did not, and one whose
    # class it left alone altogether, whose lines are R8's all the same.
    ("com.example.app.KeptActivity.a(SourceFile:2)",
     ["com.example.app.KeptActivity.helper(KeptActivity.java:41)"]),
    ("com.example.app.KeptActivity.onCreate(SourceFile:2)",
     ["com.example.app.KeptActivity.onCreate(KeptActivity.java:16)"]),
    ("com.example.app.Untouched.run(SourceFile:2)",
     ["com.example.app.Untouched.run(Untouched.java:6)"]),
    # Two methods R8 gave one name, told apart by their ranges.
    ("a.f.c(SourceFile:4)", ["com.example.app.Disk.checksumLegacy(Disk.kt:30)"]),
    # The number is read whatever the file says: R8's own name for it, the
    # mapping's id, nothing, or ART's `unavailable` before an offset.
    ("a.f.c(r8-map-id-3f9a2c1:2)", ["com.example.app.Disk.checksum(Disk.kt:13)"]),
    ("a.f.c(:2)", ["com.example.app.Disk.checksum(Disk.kt:13)"]),
    ("a.f.c(unavailable:2)", ["com.example.app.Disk.checksum(Disk.kt:13)"]),
    # A method line without ranges did not move its lines.
    ("a.g.a(SourceFile:7)", ["com.example.app.Plain.noLines(Plain.java:7)"]),
    ("a.g.b(SourceFile:2)", ["com.example.app.Plain.identity(Plain.java:2)"]),
])
def test_a_frame_comes_back_as_r8s_retrace_names_it(frame: str, named: list[str],
                                                      tmp_path: Path) -> None:
    got = retraced(RETRACE, frame, tmp_path)
    check(f"{frame}", got is not None and not got.outside
          and [f.text() for f in got.frames] == named and len(got.chains) == 1, got)


def test_a_nested_class_is_in_its_outer_classs_file(tmp_path: Path) -> None:
    # R8's `retrace` says `Store.java` here, a guess from the class's name,
    # while the mapping says where Store was written.
    got = retraced(RETRACE, "a.b$a.a(SourceFile:2)", tmp_path)
    check("the outer class's file", got is not None and [f.text() for f in got.frames]
          == ["com.example.app.Store$Companion.create(Store.kt:81)"], got)


@pytest.mark.parametrize("frame", [
    "a.z.y(SourceFile:3)",                                  # a class the mapping lacks
    "com.example.app.KeptActivity.onCreate(KeptActivity.kt:16)",  # already retraced
    "com.example.app.KeptActivity.helper(KeptActivity.kt:40)",    # the class's own name
    "java.lang.Thread.sleep(Native method)",
])
def test_a_frame_that_is_not_r8s_stays_as_it_was(frame: str, tmp_path: Path) -> None:
    check(f"{frame}", retraced(RETRACE, frame, tmp_path) is None)


@pytest.mark.parametrize("frame, named", [
    # A line in no range of its method: the class comes back, the method
    # keeps R8's name, and the line goes — R8's `retrace` keeps it.
    ("a.b.c(SourceFile:20)", "com.example.app.Store.c(Store.kt)"),
    ("a.g.b(SourceFile:9)", "com.example.app.Plain.b(Plain.java)"),
    # A method its class does not have, with a line or without.
    ("a.b.x(SourceFile:3)", "com.example.app.Store.x(Store.kt)"),
    ("a.b.x(SourceFile)", "com.example.app.Store.x(Store.kt)"),
])
def test_a_frame_the_mapping_has_no_place_for_is_called_so(frame: str, named: str,
                                                            tmp_path: Path) -> None:
    got = retraced(RETRACE, frame, tmp_path)
    check(f"{frame}", got is not None and got.outside
          and [f.text() for f in got.frames] == [named], got)


def test_without_a_line_two_methods_of_one_name_are_both(tmp_path: Path) -> None:
    # R8's `retrace` prints the first. A sample has no line either, and the
    # Marker Report names such a frame as both (see stacks.py).
    got = retraced(RETRACE, "a.f.c(SourceFile)", tmp_path)
    check("both, each as R8 kept it", got is not None and [[f.text() for f in c] for c in got.chains]
          == [["com.example.app.Disk.checksum(Disk.kt)"],
              ["com.example.app.Disk.checksumLegacy(Disk.kt)"]], got)


@pytest.mark.parametrize("build, mapping_text", [
    ("lines", lockapp.MAPPING), ("default", lockapp.MAPPING), ("offsets", lockapp.OFFSETS)])
def test_what_a_phone_wrote_comes_back_by_name(build: str, mapping_text: str,
                                               tmp_path: Path) -> None:
    main, holder = lockapp.FRAMES[build]
    blocked = retraced(mapping_text, main, tmp_path)
    check("the main thread inside Store.read, inlined into MainActivity.freeze",
          blocked is not None and [f.text() for f in blocked.frames]
          == ["com.example.locks.Store.read(Store.java:17)",
              "com.example.locks.MainActivity.freeze(MainActivity.java:21)"], blocked)
    owner = retraced(mapping_text, holder, tmp_path)
    check("the worker inside Store.hold, inlined into Holder.run",
          owner is not None and [f.text() for f in owner.frames]
          == ["com.example.locks.Store.hold(Store.java:10)",
              "com.example.locks.Holder.run(Holder.java:23)"], owner)


def test_a_lock_slice_without_lines_is_named_by_the_methods_r8_kept(tmp_path: Path) -> None:
    # `(SourceFile:-1)`: the build with offsets gives a lock slice no line.
    for frame, named in (("a.a.run(SourceFile:-1)", "com.example.locks.Holder.run(Holder.java)"),
                         ("a.b.run(SourceFile:-1)",
                          "com.example.locks.MainActivity.freeze(MainActivity.java)")):
        got = retraced(lockapp.OFFSETS, frame, tmp_path)
        check(f"{frame}", got is not None and [f.text() for f in got.frames] == [named], got)


def test_a_synthetic_class_and_a_kept_one_are_named_back(tmp_path: Path) -> None:
    got = retraced(lockapp.MAPPING, "com.example.locks.MainActivity.onCreate(SourceFile:3)",
                   tmp_path)
    check("onCreate at the line Holder.start was inlined at", got is not None
          and [f.text() for f in got.frames]
          == ["com.example.locks.Holder.start(Holder.java:13)",
              "com.example.locks.MainActivity.onCreate(MainActivity.java:16)"], got)
    path = tmp_path / "mapping.txt"
    retracer = mapping.retracer(path, {"a.c", "java.lang.Object"})
    check("a monitor's class comes back by its name",
          (retracer.real("a.c"), retracer.real("java.lang.Object"),
           retracer.real("com.example.locks.MainActivity"))
          == ("com.example.locks.Store", None, None))


@pytest.mark.parametrize("text, parsed", [
    ("SourceFile:6", ("SourceFile", 6)),
    ("Store.kt:-1", ("Store.kt", None)),
    (":-2", (None, None)),
    ("unavailable:0", ("unavailable", 0)),
    (f"{lockapp.MAP_ID}:3", (lockapp.MAP_ID, 3)),
    ("Native method", ("Native method", None)),
    ("", (None, None)),
])
def test_what_a_frames_parenthesis_says(text: str, parsed: tuple) -> None:
    check(f"{text!r}", mapping.where(text) == parsed, mapping.where(text))


def test_only_the_classes_asked_for_are_read_with_their_lines(tmp_path: Path) -> None:
    path = tmp_path / "mapping.txt"
    path.write_text(RETRACE, encoding="utf-8")
    classes, files = mapping._read_lines(path, frozenset({"a.f"}))
    check("one class, with its methods", sorted(classes) == ["a.f"]
          and sorted(classes["a.f"].methods) == ["c", "d"], classes)
    check("and every class's file, for what was inlined from it",
          files == {"com.example.app.Store": "Store.kt", "com.example.app.Disk": "Disk.kt"},
          files)


def test_a_retracer_is_read_again_once_the_mapping_changes(tmp_path: Path) -> None:
    path = tmp_path / "mapping.txt"
    path.write_text(lockapp.MAPPING, encoding="utf-8")
    first = mapping.retracer(path, ["a.a"])
    check("read once while the file stays", mapping.retracer(path, ["a.a"]) is first)
    path.write_text(lockapp.OFFSETS, encoding="utf-8")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
    again = mapping.retracer(path, ["a.a"])
    check("and again once it changes", again is not first
          and again.retrace("a.a", "run", "unavailable", 20) is not None
          and again.retrace("a.a", "run", "unavailable", 20).frames[0].line == 10)
