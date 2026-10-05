#!/usr/bin/env python3
"""The build's R8 mapping — `echolot/mapping.py`, and what the config and the report make of it.

What trace_processor does with the packet is the self-check's to pin, in
`R8 mapping: a minified build's frames come back by their names`. Here: what
is read out of a mapping.txt, what goes into the packet, and the words.
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
