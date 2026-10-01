#!/usr/bin/env python3
"""What ran in a blind spot — `echolot/stacks.py`.

The stacks are built by hand, leaf first, the way `stacks.chains` returns
them: reading them is arithmetic over frames, and a fact about a shape the
unwinder writes needs no trace. What trace_processor hands back for the
fixture's samples is the self-check's to pin, in `uninstrumented_cpu: the
samples in a blind spot name what ran there`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import anr, stacks  # noqa: E402
from echolot.stacks import Frame  # noqa: E402
from echolot.tp import Detector  # noqa: E402
from tests.support import check  # noqa: E402

APP = "/data/app/com.example.app/oat/arm64/base.odex"
BOOT = "/system/framework/arm64/boot-framework.oat"
LIBC = "/apex/com.android.runtime/lib64/bionic/libc.so"
LIBART = "/apex/com.android.art/lib64/libart.so"
LIBZ = "/system/lib64/libz.so"

# What a coroutine worker runs everything on, leaf first, down to where its
# thread began: the pool's loop, Thread.run from the framework, which comes
# back without a name, and bionic's thread start.
POOL = [Frame("kotlinx.coroutines.DispatchedTask.run", APP), Frame(None, BOOT),
        Frame("__start_thread", LIBC)]

OURS = anr.Ownership("com.example.app")


# --- the names the unwinder writes -----------------------------------------

def test_a_frame_of_the_jvm_is_told_by_its_name_and_its_file() -> None:
    check("a bare Kotlin frame",
          Frame("com.example.app.Store.save", APP).method == "com.example.app.Store.save")
    check("the return type and the arguments come off",
          Frame("void com.example.app.Store.save(java.lang.String)", APP).method
          == "com.example.app.Store.save")
    check("a lambda keeps its synthetic name",
          Frame("com.example.app.Store$save$1.invokeSuspend", APP).method
          == "com.example.app.Store$save$1.invokeSuspend")
    check("a C function is not one", Frame("deflate", LIBZ).method is None)
    check("nor a C++ one", Frame("art::ArtMethod::Invoke(art::Thread*)", LIBART).method is None)
    check("a compiler's clone has the dots and lives in a shared object",
          Frame("inflate.part.0", LIBZ).method is None)
    check("a frame with no name is nothing's", Frame(None, BOOT).method is None)


def test_the_evidence_calls_a_frame_by_its_last_two_parts_or_its_file() -> None:
    check("the package goes", Frame("com.example.app.Store.save", APP).short == "Store.save")
    check("a nested class stays whole",
          Frame("kotlinx.coroutines.scheduling.CoroutineScheduler$Worker.run", APP).short
          == "CoroutineScheduler$Worker.run")
    check("an obfuscated class keeps its last two parts", Frame("a.b.c", APP).short == "b.c")
    check("a C++ frame keeps its class and loses its arguments, in the json too",
          Frame("art::ArtMethod::Invoke(art::Thread*)", LIBART).short == "ArtMethod::Invoke"
          and Frame("art::ArtMethod::Invoke(art::Thread*)", LIBART).label
          == "art::ArtMethod::Invoke")
    check("the framework's code is called by its file, in the evidence and in the json",
          Frame(None, BOOT).short == Frame(None, BOOT).label == "[boot-framework.oat]")
    check("and a frame with no file either says so", Frame(None, None).label == "[unknown]")


@pytest.mark.parametrize("symbol, name", [
    # From the stacks of a sampled cold start on a phone.
    ("_ZN3art11ReadBarrier4MarkEPNS_6mirror6ObjectE", "art::ReadBarrier::Mark"),
    ("_ZN3artL9Field_setEP7_JNIEnvP8_jobjectS3_S3_"
     ".__uniq.201706906895676056810378034188277470683", "art::Field_set"),
    ("_ZNK3art15TypeLookupTable6LookupENSt3__117basic_string_viewIcNS1_11char_traitsIcEEEEj",
     "art::TypeLookupTable::Lookup"),
    # Template arguments in the middle of a name, and at its end.
    ("_ZNSt3__112basic_stringIcNS_11char_traitsIcEENS_9allocatorIcEEE6appendEPKcm",
     "std::__1::basic_string::append"),
    ("_ZN3art11interpreter20ExecuteSwitchImplCppILb0EEEvPNS0_17SwitchImplContextE",
     "art::interpreter::ExecuteSwitchImplCpp"),
    ("_ZN3art2gc4Heap24AllocObjectWithAllocatorILb1ELb1ENS_6mirror21SetStringCountVisitor"
     "EEEPNS5_6ObjectEPNS_6ThreadENS_6ObjPtrINS5_5ClassEEEmNS0_13AllocatorTypeERKT1_",
     "art::gc::Heap::AllocObjectWithAllocator"),
    # A local function, a constructor and a destructor.
    ("_ZL15__pthread_startPv", "__pthread_start"),
    ("_ZN3art6ThreadC2Eb", "art::Thread::Thread"),
    ("_ZN3art6ThreadD1Ev", "art::Thread::~Thread"),
    # A C function's clone, and a symbol past what is read comes back as it was.
    ("inflate.part.0", "inflate"),
    ("_Znwm", "_Znwm"),
])
def test_a_mangled_name_is_read_down_to_its_qualified_name(symbol: str, name: str) -> None:
    check(f"{symbol[:40]}… is {name}", Frame(symbol, LIBART).label == name,
          Frame(symbol, LIBART).label)


# --- what the samples of one row say ---------------------------------------

SAVED = [Frame("deflate", LIBZ), Frame(None, BOOT), Frame("okio.GzipSink.write", APP),
         Frame("com.example.app.Store.save", APP), *POOL]
POOLED = [Frame("deflate", LIBZ), Frame(None, BOOT), Frame("okio.GzipSink.write", APP), *POOL]
INTERPRETED = [Frame("nterp_helper", LIBART), Frame("com.example.app.Store.parse", APP),
               Frame("com.example.app.Store.load", APP), *POOL]
CUT = [Frame("_ZN3art11ReadBarrier4MarkEPNS_6mirror6ObjectE", LIBART), Frame(None, BOOT)]
CHAINS = {1: SAVED, 2: POOLED, 3: INTERPRETED, 4: CUT}


def test_what_ran_is_the_first_named_method_under_the_runtime_and_native_code() -> None:
    block, words = stacks.read([None, 1, 1, 1, 2, 2, 3, 4], CHAINS, OURS)
    check("every sample counted, and the ones with a stack apart",
          (block["samples"], block["with_stack"]) == (8, 7), block)
    check("zlib and the unnamed framework are passed over for the sink, the "
          "interpreter for the method it ran; a stack with no method keeps its name",
          [(e["frame"], e["samples"], e["pct"]) for e in block["leaf"]]
          == [("okio.GzipSink.write", 5, 71.4), ("art::ReadBarrier::Mark", 1, 14.3),
              ("com.example.app.Store.parse", 1, 14.3)], block["leaf"])
    check("ours: the nearest frame, a whole stack with nothing of ours, and a cut one",
          [(e["frame"], e["samples"], e.get("stack")) for e in block["ours"]]
          == [("com.example.app.Store.save", 3, None), (None, 2, "whole"),
              ("com.example.app.Store.parse", 1, None), (None, 1, "cut")], block["ours"])
    check("the evidence names two of each",
          words == "7 stacks: GzipSink.write 71%, ReadBarrier::Mark 14% · ours: Store.save 43%, none 29%",
          words)


def test_a_stack_with_nothing_named_is_named_by_its_file() -> None:
    block, words = stacks.read([1], {1: [Frame(None, BOOT), Frame(None, BOOT)]}, OURS)
    check("the file", block["leaf"][0]["frame"] == "[boot-framework.oat]", block)
    check("and it did not reach its thread's start", block["ours"][0].get("stack") == "cut", block)
    check("so the words say cut", words == "1 stack: [boot-framework.oat] 100% · ours: cut 100%",
          words)


def test_a_whole_stack_ends_where_its_thread_began() -> None:
    main = [Frame("com.example.app.Main.run", APP), Frame(None, BOOT),
            Frame("__libc_init", LIBC)]
    mangled = [Frame("deflate", LIBZ), Frame("_ZL15__pthread_startPv", LIBC)]
    block, _ = stacks.read([1, 2], {1: main, 2: mangled}, None)
    check("the process's start for the main thread, a mangled thread start for a worker",
          [e.get("stack") for e in block["ours"]] == ["whole"], block["ours"])


def test_a_tie_goes_to_a_frame_then_to_a_whole_stack_then_by_the_name() -> None:
    chains = {1: [Frame("com.example.app.B.run", APP), *POOL],
              2: [Frame("com.example.app.A.run", APP), *POOL],
              3: POOLED, 4: CUT}
    block, words = stacks.read([1, 2, 3, 4], chains, OURS)
    check("frames first, in order, then none, then cut",
          [(e["frame"], e.get("stack")) for e in block["ours"]]
          == [("com.example.app.A.run", None), ("com.example.app.B.run", None),
              (None, "whole"), (None, "cut")], block["ours"])
    check("and the same order in the words", words.endswith("ours: A.run 25%, B.run 25%"), words)


def test_a_build_under_a_suffix_of_its_own_still_finds_the_apps_frames(tmp_path) -> None:
    # A benchmark build installs as com.example.app.beta; its code stays in
    # com.example.app. A match on the package alone would call it all nobody's.
    library = [Frame("com.thirdparty.json.Encoder.encode", APP), *SAVED]
    guess, _ = stacks.read([1], {1: library}, anr.Ownership("com.example.app.beta"))
    check("without a checkout the package's root is enough, and an unlisted library passes",
          guess["ours"][0]["frame"] == "com.thirdparty.json.Encoder.encode", guess["ours"])

    source = tmp_path / "app/src/main/kotlin/com/example/app/Store.kt"
    source.parent.mkdir(parents=True)
    source.write_text("package com.example.app\n\nclass Store\n", encoding="utf-8")
    ours = stacks.ownership("com.example.app.beta", tmp_path)
    check("a checkout declares where the code is", ours.declared == frozenset({"com.example.app"}),
          ours)
    settled, words = stacks.read([1], {1: library}, ours)
    check("and then the library is somebody else's, and Store.save is ours",
          settled["ours"][0]["frame"] == "com.example.app.Store.save", settled["ours"])
    check("the words say the same", words.endswith("ours: Store.save 100%"), words)


def test_a_native_frame_is_nobodys_even_without_a_checkout() -> None:
    # Without a checkout, anything outside the platform lists is claimed as
    # the app's. A C function is in no package, and would be claimed.
    native = [Frame("deflate", LIBZ), Frame("inflate.part.0", LIBZ), *POOL]
    block, words = stacks.read([1, 1], {1: native}, OURS)
    check("nothing of ours on a whole stack", block["ours"] == [
        {"frame": None, "samples": 2, "pct": 100.0, "stack": "whole"}], block["ours"])
    check("and the row says so", words.endswith("ours: none 100%"), words)


def test_what_a_row_says_when_the_samples_name_nothing() -> None:
    block, words = stacks.read([], {}, None)
    check("no samples at all", words == "no samples" and block["samples"] == 0, (words, block))
    block, words = stacks.read([None, None], {}, None)
    check("samples that came without a stack",
          words == "2 samples, none with a stack" and block["with_stack"] == 0, (words, block))
    block, words = stacks.read([None], {}, None)
    check("and one of them", words == "1 sample, none with a stack", words)


def test_a_frame_the_mapping_named_as_several_methods_is_all_of_them() -> None:
    # R8 gives overloads one name, and trace_processor names such a frame back
    # as every method it can be.
    both = Frame("com.example.app.Disk.checksum | com.example.app.Disk.checksumLegacy", APP)
    check("a method of the JVM, each part", both.method == both.name, both.method)
    check("each part short", both.short == "Disk.checksum | Disk.checksumLegacy", both.short)
    mixed = Frame("com.example.app.Disk.checksum | not a method", APP)
    check("and not one when a part is not", mixed.method is None, mixed.method)
    block, words = stacks.read([1], {1: [both, *POOL]}, OURS)
    check("it is ours, what ran, and named whole in the json",
          block["ours"][0]["frame"] == block["leaf"][0]["frame"] == both.name, block)
    check("the words keep both", words.endswith("ours: Disk.checksum | Disk.checksumLegacy 100%"),
          words)


def test_a_long_name_is_cut_in_the_words_and_kept_whole_in_the_json() -> None:
    # A lambda's synthetic name, the shape one had on a phone.
    lam = ("com.example.app.sync.HeartBeatController.lambda$registerHeartBeat$0"
           "$com-example-app-sync-HeartBeatController")
    block, words = stacks.read([1], {1: [Frame(lam, APP), *POOL]}, OURS)
    check("the json has it whole", block["leaf"][0]["frame"] == lam, block["leaf"])
    shown = words.split(": ", 1)[1].split(" 100%")[0]
    check(f"the words at most {stacks.LONGEST} characters",
          len(shown) == stacks.LONGEST and shown.endswith("…"), shown)


def test_a_share_too_small_to_show_says_so_and_the_lists_are_cut() -> None:
    chains = {i: [Frame(f"f{i}", LIBZ), Frame("__start_thread", LIBC)] for i in range(12)}
    samples = [0] * 200 + list(range(1, 12))
    block, words = stacks.read(samples, chains, OURS)
    check("half a percent and more rounds up; less says so",
          words.startswith("211 stacks: f0 95%, f1 <1%"), words)
    check("ten functions are kept", len(block["leaf"]) == stacks.KEEP, block["leaf"])
    check("and the rest are counted", block["more"] == {"leaf": 2}, block.get("more"))


# --- the third query --------------------------------------------------------

DETECTOR = """\
-- @id: demo
-- @title: Demo
-- @param: min_ms = 16

SELECT thread_name AS location, COUNT(*) AS count
FROM _slice_win
GROUP BY thread_name
HAVING SUM(dur) >= {{min_ms}} * 1000000
LIMIT 20;

-- @intervals

SELECT ts, dur FROM _slice_win WHERE dur >= {{min_ms}} * 1000000;

-- @samples
--
-- a comment of its own, with a semicolon; in it

SELECT t.name AS location, s.callsite_id
FROM _samples_by_slice s JOIN thread t USING (utid)
WHERE s.dur >= {{min_ms}};
"""


def test_the_third_query_comes_off_the_file(tmp_path: Path) -> None:
    path = tmp_path / "demo.sql"
    path.write_text(DETECTOR, encoding="utf-8")
    d = Detector.from_file(path)
    check("the second query ends where the third begins",
          "@samples" not in d.intervals_sql and "callsite_id" not in d.intervals_sql,
          d.intervals_sql)
    check("the first is untouched", "@intervals" not in d.sql and "_samples" not in d.sql, d.sql)
    _, params = d.render({"min_ms": 3})
    third = d.render_samples(params)
    check("the third takes the thresholds the first ran with",
          third is not None and "s.dur >= 3" in third, str(third))

    plain = tmp_path / "plain.sql"
    plain.write_text(DETECTOR.split("-- @samples")[0], encoding="utf-8")
    check("a detector without one has none to run",
          Detector.from_file(plain).render_samples(params) is None)

    twice = tmp_path / "twice.sql"
    twice.write_text(DETECTOR + "\n-- @samples\nSELECT 1;\n", encoding="utf-8")
    with pytest.raises(ValueError, match="two `-- @samples` sections"):
        Detector.from_file(twice)
