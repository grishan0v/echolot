"""`echolot anr` on the records it summed up wrong: a drop box print with five
entries, an older ART dump that prints its statistics first, a thread standing
inside a library, a queue from Play Console, a process named like the app, and
the capital of one item.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot import anr  # noqa: E402
from tests.support import check  # noqa: E402
from tests.test_anr import DROPBOX, PLAY, run  # noqa: E402

ENTRY = DROPBOX[DROPBOX.index("=" * 40):]


def test_every_entry_of_a_drop_box_print_is_counted(tmp_path: Path) -> None:
    five = DROPBOX + "\n" + "\n".join([ENTRY] * 4)
    rep = anr.parse(five)
    check("five", rep.entries == 5, rep.entries)
    check("and the first one read alone", len(rep.threads) == len(anr.parse(DROPBOX).threads))
    path = tmp_path / "anr.txt"
    path.write_text(five, encoding="utf-8")
    code, out, _ = run("anr", str(path), "--json")
    check("in the json too", code == 0 and json.loads(out)["entries"] == 5, out[:400])


def test_an_older_art_dump_s_statistics_are_no_header() -> None:
    older = DROPBOX.replace(
        "Cmd line: com.example.app\n",
        "Cmd line: com.example.app\n"
        "Build fingerprint: 'generic/vbox86p:9/PPR1/1234:user/release-keys'\n"
        "ABI: 'x86_64'\n"
        "Build type: optimized\n"
        "Zygote loaded classes=8934 post zygote classes=68\n"
        "Intern table: 44318 strong; 535 weak\n"
        "JNI: CheckJNI is off; globals=402 (plus 31 weak)\n"
        "Libraries: /system/lib64/libandroid.so (26)\n"
        "Heap: 49% free, 2MB/4MB; 31086 objects\n"
        "Total time spent in GC: 1.442ms\n")
    rep = anr.parse(older)
    check("no runtime statistic in the head",
          not {"Build fingerprint", "Intern table", "JNI", "Heap",
               "Total time spent in GC"} & set(rep.head), sorted(rep.head))
    check("none of it unread", rep.unread == [], rep.unread)
    check("passed over by position, and said",
          anr.summary(rep)["unread"]["before_the_threads"] == 9
          and "before and after the threads" in anr.render(rep), anr.summary(rep)["unread"])


LIBRARY_TOP = """\
----- pid 4100 at 2026-08-20 21:22:39.180419343+0200 -----
Cmd line: com.example.app

DALVIK THREADS (1):
"main" prio=5 tid=1 Runnable
  | group="main" sCount=0 ucsCount=0 flags=0 obj=0x71d88e28 self=0x7a375f7800
  at com.google.gson.internal.bind.ReflectiveTypeAdapterFactory$Adapter.read(ReflectiveTypeAdapterFactory.java:220)
  at com.google.gson.Gson.fromJson(Gson.java:932)
  at android.os.Handler.dispatchMessage(Handler.java:106)
  at android.os.Looper.loop(Looper.java:288)

----- end 4100 -----
"""


def test_a_thread_standing_inside_a_library_is_a_lead() -> None:
    rep = anr.parse(LIBRARY_TOP)
    data = anr.summary(rep)
    check("listed as the nearest", [n["thread"] for n in data["nearest"]] == ["main"],
          data["nearest"])
    check("and the frame where the library was entered is printed",
          any("Gson.fromJson" in f for f in data["main"]["stack"]), data["main"]["stack"])


def test_a_queued_thread_is_not_among_the_working_ones() -> None:
    rep = anr.parse(PLAY)
    text = anr.render(rep)
    working = text.split("## Threads that were doing something", 1)
    check("the waiter is not listed again",
          len(working) == 1 or "DefaultDispatcher-worker-3" not in working[1].split("##")[0],
          text)
    check("nor in the json",
          "DefaultDispatcher-worker-3" not in [t["name"] for t in anr.summary(rep)["working"]])


def test_only_the_app_s_processes_are_this_app() -> None:
    rep = anr.parse(DROPBOX.replace(
        "  38% 5339/surfaceflinger:",
        "  21% 6100/com.example.application: 15% user + 6% kernel / faults: 900 minor\n"
        "  20% 6200/com.example.app:remote: 15% user + 5% kernel / faults: 900 minor\n"
        "  38% 5339/surfaceflinger:"))
    text = anr.render(rep)
    check("a longer name is another app",
          "`com.example.application`  ← this app" not in text, text)
    check("and the app's own process is the app",
          "`com.example.app:remote`  ← this app" in text, text)


def test_every_gap_starts_with_a_capital(tmp_path: Path) -> None:
    rep = anr.parse(DROPBOX)
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src/Other.kt").write_text("package com.example.app\nclass Other\n", encoding="utf-8")
    text = anr.render(rep, anr.locate(rep, root))
    check("Where", "- Where " in text and "- where " not in text, text[-900:])
