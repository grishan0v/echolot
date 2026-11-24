#!/usr/bin/env python3
"""The ANR report from the field, read the way the Marker Report reads a trace.

An ANR arrives as a thread dump: every thread in the process with its stack,
frozen at the moment the system gave up waiting. Fifty threads on a quiet app
and three hundred on a busy one, and almost all of them are asleep. Handed to a
person or an agent whole, it produces the same thing an 80 MB trace does —
confident guesses at whichever frame happened to be on top.

This module does to the dump what the detectors do to the trace: strikes out
everything that was doing nothing and leaves the few threads that were.

Three sources print the same dump: an export from Crashlytics, a cluster in
Play Console, and the device's own record under
`adb shell dumpsys dropbox --print data_app_anr`. What they agree on is the
**frames** — java as `at package.Class.method(File.java:NN)`, native as
`#NN pc 0x… lib.so (symbol + offset)`. The thread signature is each source's
own: Crashlytics writes `main (blocked):tid=1 systid=8413 | waiting to lock …`,
ART in dumpsys writes `"main" prio=5 tid=1 Blocked` with a `| group=…` line
under it. So one reader for the frames, one per source for the signature — the
export and the device's own record, with the source decided by which of them
the file announces its threads in.

The device's record is the one with a `Subject`: the reason the system fired,
which the export drops. It also holds a block per process, and only the one
that hung is read.

The strongest thing in the file is the lock chain, and it needs no trace at
all. A monitor held by a thread that is itself parked on a blocking call, with
the main thread queued behind it, is the mechanism rather than a coincidence.
On ten reports from a live app it appeared four times and unfolded to a line in
a file every time. Everything else the dump says is a snapshot: where a thread
was standing five seconds in, which is not necessarily where the time went.

    echolot anr report.txt
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import mapping as mapping_mod
from .domains import source_files

# --- the format -------------------------------------------------------------
#
# Frames are what the sources agree on. Everything around them — how a thread
# is announced, where the lock note lives, what a native frame is prefixed
# with — is each source's own, so it is declared per source rather than
# branched on inside the reader.
#
# `# Key: value` is Crashlytics, bare `Key: value` is the dropbox record. One
# pattern reads both headers; only the thread body needed splitting.
_HEADER = re.compile(r"^#?\s*(?P<key>[A-Za-z][A-Za-z _-]*):\s?(?P<value>.*)$")

# The dropbox record wraps the dump: an ANR trace holds one block per process,
# starting with the one that hung, and every block names itself.
_PROCESS = re.compile(r"^-{2,}\s*pid (?P<pid>\d+) at .*-{2,}\s*$")
_CMDLINE = re.compile(r"^Cmd line: (?P<cmd>\S+)")
# `dumpsys dropbox --print <tag>` concatenates every entry it holds, each
# opened by a rule and then its time and tag: `2026-08-20 21:22:45
# data_app_anr (compressed text, 46727 bytes)`.
_ENTRY = re.compile(r"^={20,}\s*$")
_ENTRY_HEAD = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\s+\S+\s+\(")
# One line of the record's own CPU table, which sits above the thread dump:
#   57% 5662/system_server: 31% user + 25% kernel / faults: 22124 minor
# It is the only thing in an ANR report that says what the rest of the device
# was doing, and a machine that was busy is a different story from an app that
# blocked itself.
_LOAD = re.compile(
    # The name runs to the colon that has a space after it: a kernel worker is
    # `sugov:4`, and stopping at the first colon turns every one of them into
    # the same row.
    r"^\s*(?P<share>[\d.]+)% (?P<pid>\d+)/(?P<name>.+?):\s(?P<split>.*)$"
)
# How many threads ART is about to print for the process, above its block;
# against the threads read, it is what shows a dump that was cut short.
_LISTED = re.compile(r"^DALVIK THREADS \((?P<n>\d+)\):")
# What the drop box writes where it cut an entry to its size limit. A dump of
# hundreds of threads reaches that limit.
_TRUNCATED = re.compile(r"^\[\[TRUNCATED\]\]\s*$")
# Where a source stopped writing one thread's stack: the Crashlytics tools
# give most threads of an event cut after eighteen or nineteen frames, and
# say so with this line.
_OMITTED = re.compile(r"^\s*\.{3}\s*(?:\d+\s+)?(?:more\s+)?frames?\s+omitted\s*(?:\.{3})?\s*$")
# The record's own furniture: block rules, the thread count above a block, and
# the runtime's note about how long suspending everything took. Skipped by
# name so that a line which is genuinely new still reaches the unread count.
_SCAFFOLD = re.compile(
    r"^(?:"
    r"-{2,}.*-{2,}\s*"                       # the rule around a process block
    r"|DALVIK THREADS \(\d+\):"              # how many are in the one below
    r"|suspend all histogram:.*"             # what stopping them all cost
    r"|CPU usage from .*"                    # the load table's heading
    r"|\s*[+-]?[\d.]+% .*"                   # and one line of it
    r"|\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \w+ \(.*\)\s*"   # the entry's own headline
    r")$"
)
# The few of the runtime's statistics that say how full the heap was and how
# much the app has waited for the collector: `Heap: 2% free, 251MB/256MB;
# 3456789 objects`, `Max memory 256MB`, `Total blocking GC count: 12`. Read
# by name, for a main thread that was waiting for a garbage collection, and
# so no longer among the lines passed over by position.
_HEAP_LINE = re.compile(
    r"^(?P<key>Heap|Max memory|Free memory until OOME"
    r"|Total time waiting for GC to complete|Total blocking GC count"
    r"|Total blocking GC time)(?::\s*|\s+)(?P<value>\S.*?)\s*$"
)


@dataclass(frozen=True)
class Source:
    """One producer's way of printing a thread dump.

    `signature` must yield `name` and `state`, and any of `tid`, `systid`,
    `note`. `lock` is applied to the note when the source puts it there and to
    every frame line when it does not.
    """
    name: str
    signature: re.Pattern[str]
    frame: re.Pattern[str]
    native: re.Pattern[str]
    lock: re.Pattern[str]
    held: re.Pattern[str] | None = None
    sys_tid: re.Pattern[str] | None = None
    lock_on_signature: bool = False
    # Lines inside a thread block that are read and carry nothing this needs.
    # Named rather than ignored by default, so a line that is genuinely new
    # still reaches the unread count.
    noise: re.Pattern[str] | None = None
    # Whether a thread's body is indented under its signature. Where it is, an
    # unindented line ends the thread — which is what tells the threads apart
    # from the runtime summary printed after the last of them.
    body_indented: bool = False


# Verified against ten exports from a live application.
#
# The tail after `|` carries the monitor a blocked thread wants and the tid
# holding it. It is optional because the first sample report had no blocked
# thread at all — a second pattern would have made that report unreadable
# rather than merely quiet.
CRASHLYTICS = Source(
    name="crashlytics",
    signature=re.compile(
        r"^(?P<name>.+?) \((?P<state>[^)]*)\):"
        r"tid=(?P<tid>-?\d+) systid=(?P<systid>-?\d+)\s*"
        r"(?:\|\s*(?P<note>.*))?$"
    ),
    frame=re.compile(r"^\s+at (?P<frame>.+)$"),
    native=re.compile(r"^\s*(?:native:\s*)?#\d+ pc 0x[0-9a-f]+ (?P<lib>\S+)"),
    # The tail naming the owner is ART's to give, when it could read the owner
    # at dump time; a note without it still says which monitor.
    lock=re.compile(
        r"waiting to lock <(?P<addr>0x[0-9a-f]+)> "
        r"\((?P<cls>[^)]*)\)(?: held by thread (?P<owner>-?\d+))?"
    ),
    lock_on_signature=True,
)

# What ART itself prints, which is what `dumpsys dropbox --print data_app_anr`
# and the files under `/data/anr/` carry.
#
# Checked against a record made on purpose: an app that takes a monitor on a
# worker and then blocks the main thread on it, frozen on an Android 13 phone
# and read back out of the drop box. Every shape below is in that record.
#
# Three signature forms, all ART's: a managed thread carries `prio` and `tid`,
# one the runtime never attached carries `prio` and says so, and one still
# starting carries a parenthetical after its state. Leaving room for that last
# one is not cosmetic — without it the thread is swallowed into the block above
# and disappears from the count.
DUMPSYS = Source(
    name="dumpsys",
    signature=re.compile(
        r'^"(?P<name>[^"]*)"(?:\s+daemon)?\s+'
        r"(?:prio=\d+\s+tid=(?P<tid>-?\d+)\s+(?P<state>[A-Za-z]\w*)"
        r"|prio=\d+\s+\((?P<detached>not attached)\)"
        r"|sysTid=(?P<systid>\d+))"
        # A state can carry a parenthetical after it — `Native (still starting
        # up)`. Without room for it the thread is not merely mis-stated, it is
        # swallowed into the block above and vanishes from the count.
        r"(?:\s+\(.*\))?\s*$"
    ),
    frame=re.compile(r"^\s+at (?P<frame>.+)$"),
    native=re.compile(r"^\s*(?:native:\s*)?#\d+ pc [0-9a-fx]+\s+(?P<lib>\S+)"),
    # On its own line under the frame that could not enter, and the class comes
    # with an article ART puts there: `(a java.lang.Object)`. ART appends
    # `held by thread N` only when it could read the owner at dump time; a
    # note without it was not read at all, and the holder that printed
    # `- locked` for that address went unnamed.
    lock=re.compile(
        r"^\s*- waiting to lock <(?P<addr>0x[0-9a-f]+)>"
        r"\s+\(a (?P<cls>[^)]*)\)(?: held by thread (?P<owner>-?\d+))?"
    ),
    # What a thread already holds. Crashlytics prints nothing of the kind. It
    # is the second way to the holder: when the note names no tid, and when
    # the tid it names is not in the dump.
    held=re.compile(r"^\s*- locked <(?P<addr>0x[0-9a-f]+)>"),
    sys_tid=re.compile(r"^\s*\|\s*sysTid=(?P<systid>\d+)"),
    # `waiting on` and `sleeping on` name a monitor the thread is waiting
    # *inside*, having released it — the opposite of holding, and no use to
    # the chain. The rest is the runtime's own bookkeeping.
    noise=re.compile(
        r"^\s*(?:- (?:waiting on|sleeping on) <0x[0-9a-f]+>.*"
        r"|\(no managed stack frames\)"
        r"|DumpLatencyMs:.*)$"
    ),
    body_indented=True,
)

# What Play Console shows for an ANR cluster. Checked against a live export.
#
# Close to ART's and not the same: no `prio`, no `sysTid`, no header of any
# kind, and the state is spelled in words with a space in it — `Timed Waiting`
# rather than `TimedWaiting`. Frames carry a space before the parenthesis, and
# native frames pad their columns.
#
# The one that matters: **no lock ownership**. Play Console strips
# `held by thread N` and the `- locked` lines with it, so an export from here
# can say a thread is blocked and never say by whom. The strongest finding this
# reader has is unavailable from this source, and that is worth knowing before
# choosing which console to export from.
PLAY = Source(
    name="play",
    signature=re.compile(
        r'^"(?P<name>[^"]*)" tid=(?P<tid>-?\d+) (?P<state>.+?)\s*$'
    ),
    frame=re.compile(r"^\s+at (?P<frame>.+)$"),
    native=re.compile(r"^\s*#\d+\s+pc\s+[0-9a-fx]+\s+(?P<lib>\S+)"),
    # There is nothing to read. A pattern that cannot match keeps the body
    # reader uniform instead of teaching it that a source may have no locks.
    lock=re.compile(r"(?!)"),
    body_indented=True,
)

SOURCES = (CRASHLYTICS, DUMPSYS, PLAY)

# What an agent's Crashlytics tool returns for one event, rather than what the
# console exports. Not a dump but a record: the issue Crashlytics filed it
# under, the build and its revision, whether the app was in the foreground,
# the keys the app set — and `threads`, which in the shape the tools give is
# the one thread Crashlytics blamed, written as text:
#
#   Thread: main
#   at __epoll_pwait
#   at android.os.MessageQueue.nativePollOnce (Native method)
#   at android.os.MessageQueue.next (MessageQueue.java:355)
#   ... frames omitted ...
#
# No state, no tid, no lock note and no other thread: 29 of 30 events taken
# from a large app's top issues had exactly one. Its frames are read as any
# other source's, and the report says what such an input cannot show (see
# `_gaps`). The signature below is what `detect` counts when the thread text
# is given alone; the record itself is told by its fields (`_event_data`).
EVENT = Source(
    name="crashlytics-event",
    signature=re.compile(r"^\s*Thread:\s*(?P<name>\S.*?)\s*$"),
    frame=re.compile(r"^\s*at\s+(?P<frame>\S.*?)\s*$"),
    native=re.compile(r"(?!)"),
    lock=re.compile(r"(?!)"),
)

# Packages that belong to the platform, the runtime and the libraries every app
# carries — a stack of twenty `java.util.concurrent` frames says nothing about
# the app that owns it.
#
# Which frames are the app's own is this list's answer only when there is no
# checkout to ask, and a list has a hole in it: a library it has not heard of
# reads as the app's. `dagger`, `koin` and `sentry` did, and their frames came
# out as the project's code the checkout was missing — the sign of a report
# from another build, given on the build that froze. With a checkout the
# packages its sources declare decide instead (see `Ownership`), and this list
# is only a veto there.
#
# Two tiers, because they read differently. CORE is the platform and the
# runtime: a frame there is a dead end, nobody configures `android.os.Looper`.
# LIBRARIES are what the app pulls in and drives — WorkManager, coroutines,
# OkHttp — and a frame there is a pointer to the app's own setup of that
# library. A working thread with nothing of the app on its stack and
# `androidx.work.impl.Schedulers` four frames down was reported as "every
# frame belongs to the platform or a library", which is true and closes the
# door on the one lead the dump had.
CORE = (
    "java.", "javax.", "jdk.", "sun.", "libcore.", "dalvik.",
    "android.", "com.android.", "kotlin.",
)
LIBRARIES = (
    "androidx.", "kotlinx.",
    "com.google.", "okhttp3.", "okio.", "retrofit2.", "io.reactivex.",
)
PLATFORM = CORE + LIBRARIES


def _package_of(frame: str) -> str:
    """The package a frame's class is declared in: `pkg` of `pkg.Class$1.run(…)`."""
    owner = frame.split("(", 1)[0].strip().rsplit(".", 1)[0].split("$", 1)[0]
    return owner.rsplit(".", 1)[0] if "." in owner else ""


@dataclass(frozen=True)
class Ownership:
    """Which frames are this project's own code, and what that was decided by.

    From outside a checkout it is a guess: the report's own package, and
    anything not under a root in the lists above. That guess is where a
    library nobody listed came through.

    A checkout settles it instead. Its sources say which packages they are
    in, and a frame in none of them, nor below one, nor in the report's own
    package, is somebody else's code whether or not the list has heard of the
    library. The list is still consulted there, as a veto: a test stub that
    declares `package android.util` does not make `android.util.Log` the
    project's.

    A frame R8 renamed into a package of its own — `a.b.c(SourceFile:12)` —
    is in nothing a checkout declares, and reads as somebody else's once one
    is read. With the build's mapping such a report is retraced before
    anything is asked of it (see `retrace`).
    """
    package: str = ""
    # The packages the checkout's sources declare. None when no checkout was
    # read, and when the one that was declares nothing.
    declared: frozenset[str] | None = None

    @property
    def prefixes(self) -> tuple[str, ...]:
        """The report's own package, and — without a checkout — its root.

        Needed first for an app whose package sits under a platform root,
        which the lists alone would strike out. Code under a second root of
        the same company — `ru.example.app` shipping modules as
        `com.example.*`, which is what the sample reports do — needs nothing
        here: without a checkout it is not platform and survives on that, and
        a checkout declares it.

        The root goes once a checkout is read. It was a guess at where the
        rest of the project lives, and kept, it claims what is not here: a
        company SDK under the same root, counted as this checkout's missing
        code and so as a sign of the wrong build.
        """
        if not self.package:
            return ()
        parts = self.package.split(".")
        if self.declared is not None or len(parts) < 2:
            return (self.package,)
        return (self.package, ".".join(parts[:2]))

    def claims(self, frame: str) -> bool:
        """Whether this frame is the project's own code."""
        # Up to the dot: `com.example.app` is not the root of
        # `com.example.application`.
        if frame.startswith(tuple(p + "." for p in self.prefixes)):
            return True
        if frame.startswith(PLATFORM):
            return False
        if self.declared is None:
            return True
        package = _package_of(frame)
        while package:
            if package in self.declared:
                return True
            package = package.rpartition(".")[0]
        return False


@dataclass
class Lock:
    """The monitor a blocked thread wants, and who is holding it.

    `owner` is the tid the note names, and empty when it names none.
    """
    addr: str
    cls: str
    owner: str = ""

    def __post_init__(self) -> None:
        # The pattern gives None when the note names no tid.
        self.owner = self.owner or ""


@dataclass
class Thread:
    name: str
    state: str
    tid: str = ""
    systid: str = ""
    # Which process block this thread came from. Only the dropbox record has
    # more than one; an ANR trace dumps the app that hung and its neighbours.
    process: str = ""
    frames: list[str] = field(default_factory=list)
    native: list[str] = field(default_factory=list)
    lock: Lock | None = None
    # Monitors this thread already holds, when the source says so.
    held: list[str] = field(default_factory=list)
    # Lines inside the block that matched neither a frame nor anything else.
    # Kept rather than dropped: a format that grew a line we do not read should
    # say so out loud.
    unread: list[str] = field(default_factory=list)
    # How many frames were read before the source wrote `... frames omitted
    # ...` and stopped: the stack goes on below, and nothing here says how.
    # None when the stack was given whole.
    cut: int | None = None
    # How many native frames came before the first managed one: what sits
    # above the top managed frame. A source that prints the two interleaved,
    # as a native unwinder does, is otherwise read as two separate lists.
    above: int = 0

    @property
    def dumping(self) -> int:
        """How many native frames at the top are the unwinder taking the dump.

        A stack written by a native unwinder has its own signal handler on
        top — `unwindstack::SignalHandler`, `unwindstack::ThreadEntry::Wait`,
        and the futex they wait in. That is the act of taking the dump, not
        anything the thread was doing, and no mask is decided by it.
        """
        last = max((i for i, line in enumerate(self.native[:self.above])
                    if "unwindstack::" in line), default=-1)
        return last + 1

    @property
    def own_native(self) -> list[str]:
        """The native frames that are the thread's own — see `dumping`."""
        return self.native[self.dumping:]

    @property
    def top(self) -> str:
        """The frame this thread was standing on, java preferred."""
        if self.frames:
            return self.frames[0]
        native = self.own_native or self.native
        return native[0] if native else "(no frames)"

    def own(self, ours: Ownership) -> list[str]:
        """The frames that are the project's own code — see `Ownership`."""
        return [f for f in self.frames if ours.claims(f)]

    def nearest(self, ours: Ownership) -> str:
        """The frame closest to the app: its own, else a library's, else the top.

        The top of a working thread is almost always `BinderProxy.transactNative`
        or `Unsafe.park` — true and useless alone. What the thread was doing
        sits a few frames down, in the app when the app is there and in a
        library it drives when it is not: `SystemJobScheduler.cancel` says
        WorkManager was rescheduling, and the top frame says a binder call.
        """
        own = self.own(ours)
        if own:
            return own[0]
        return next((f for f in self.frames if not f.startswith(CORE)), self.top)


@dataclass(frozen=True)
class Event:
    """What a Crashlytics event says around its threads, which no dump does.

    `title` and `subtitle` are the issue's, as Crashlytics wrote them: the
    blamed frame, and its own reading of the mechanism — "a deadlock involving
    the main thread", "root cause unknown". `revision` is the build's commit
    from `buildStamp`, empty when the build stamped none. `custom_keys` are the
    names of the keys the app set, and only the names: some of the values are
    the app's identifiers of a user or a session, which a report has no
    business copying (#146).
    """
    title: str = ""
    subtitle: str = ""
    process_state: str = ""
    revision: str = ""
    custom_keys: tuple[str, ...] = ()


@dataclass
class Report:
    head: dict[str, str]
    threads: list[Thread]
    source: str = CRASHLYTICS.name
    # Lines outside every thread block that nothing matched.
    unread: list[tuple[int, str]] = field(default_factory=list)
    # Threads belonging to other processes in the same record, and further ANR
    # entries after the one that was read. Both are counted rather than merged:
    # a second process is not this app, and a second entry is a second ANR.
    elsewhere: int = 0
    entries: int = 1
    # Lines inside a process block before its first thread: an older ART
    # prints its runtime statistics there rather than after the threads.
    preamble: int = 0
    # (share of a CPU, process name) from the record's own table, biggest
    # first. Empty for a source that does not print one.
    load: list[tuple[float, str]] = field(default_factory=list)
    # Lines after the threads that were passed over by position rather than by
    # name: the runtime's statistics and the record's own furniture.
    skipped: int = 0
    # The drop box cut the entry at its size limit, and how many threads ART
    # said it would print for each process, by `Cmd line:` name.
    truncated: bool = False
    listed: dict[str, int] = field(default_factory=dict)
    # What `chains()` worked out, kept once. Not part of the report's content
    # and not compared with anything: see `chains`.
    _chains: "list[Chain] | None" = field(default=None, repr=False, compare=False)
    # The packages the checkout declares, once `locate` has read one — what
    # `Ownership` decides by. Not the report's content either.
    declared: frozenset[str] | None = field(default=None, repr=False, compare=False)
    # What the build's mapping made of the frames R8 wrote, once `retrace`
    # has run: how many it named back, how many it had no place for, and the
    # map id the frames carry when it is not the mapping's. None when no
    # mapping was given.
    retraced: dict[str, Any] | None = None
    # The retraced frames whose file is echolot's guess, `<Class>.java`, for
    # a class the mapping names no file of: see `mapping.Frame.guessed`.
    guessed: set[str] = field(default_factory=set, repr=False, compare=False)
    # The runtime's own heap lines, by the `Cmd line:` of the block they were
    # in: see `heap`.
    heaps: dict[str, dict[str, str]] = field(default_factory=dict)
    # What a Crashlytics event says beside its threads, when that is what was
    # read; None for a dump.
    event: Event | None = None
    # The commit the checkout is at, read by `locate` when the event names
    # the build's revision to compare it with; None otherwise, and outside
    # a git checkout.
    checkout: str | None = field(default=None, compare=False)

    @property
    def heap(self) -> dict[str, str]:
        """What the runtime said of its heap, for the process that hung.

        ART prints a `Heap:` line and its collection totals into the device's
        record, after the threads on a recent build and before them on an
        older one, in every process block. Only the named lines in
        `_HEAP_LINE` are kept, as the runtime wrote them. Empty for an export
        from a console, which carries the threads alone.
        """
        process = next((t.process for t in self.threads if t.process), "")
        return self.heaps.get(process, {})

    @property
    def package(self) -> str:
        """Crashlytics names the application; the dropbox record names the
        process that hung, which is the same string for a single-process app
        and the right one to scope to when it is not. A trace with no header
        at all names it only in `Cmd line:`, and `_scoped` copies that up."""
        for key in ("Application", "Process", "Package"):
            value = self.head.get(key, "")
            if value:
                return value.split()[0]
        return ""

    @property
    def reason(self) -> str:
        """Why the system fired it. The dropbox record has this; an export
        from Crashlytics does not, checked across ten of them."""
        return self.head.get("Subject", "") or self.head.get("Reason", "")

    @property
    def lock_notes(self) -> bool:
        """Whether any thread in this file says what it waits on or holds.

        An export that carries no lock note at all — Play Console strips
        them, and so do some Crashlytics exports — cannot yield a monitor
        chain, and an empty chain list then means "this file cannot say",
        not "nothing was blocked". The two were reported the same way, and a
        reader told the user "no lock chain" as a fact about the freeze.
        """
        return any(t.lock is not None or t.held for t in self.threads)

    @property
    def ownership(self) -> Ownership:
        """What counts as this project's own code in this report."""
        return Ownership(self.package, self.declared)

    @property
    def cut(self) -> int | None:
        """How many of the process's threads the dump is missing, when it
        was cut short: ART's own count for the block, less the threads read.
        None when the entry was not cut and the count adds up."""
        process = next((t.process for t in self.threads if t.process), "")
        listed = self.listed.get(process)
        short = max(listed - len(self.threads), 0) if listed is not None else 0
        if not self.truncated and not short:
            return None
        return short

    def by_tid(self) -> dict[str, Thread]:
        """Threads by tid — the id `held by thread N` refers to.

        A thread the runtime never attached has no tid to be referred to by,
        and an empty key would collect all of them into one.
        """
        return {t.tid: t for t in self.threads if t.tid}

    @property
    def main(self) -> Thread | None:
        """The main thread: the one named `main`, else the one running
        `ActivityThread.main` under whatever name the file gave it."""
        return next((t for t in self.threads if t.name == "main"),
                    next((t for t in self.threads if is_main(t)), None))


_ACTIVITY_THREAD = "android.app.ActivityThread.main("


def is_main(thread: Thread) -> bool:
    """Whether this is the app's main thread.

    ART's own dump always calls it `main`. A stack written by a native
    unwinder, as Crashlytics gives some events, calls it by the name the
    kernel knows it by: the last fifteen characters of the process. Read by
    its name alone, such a thread was a background one for every rule after
    that — every idle mask applied to it, and the report had no main thread.
    Its stack says what it is: `ActivityThread.main` runs only there.
    """
    return thread.name == "main" or any(f.startswith(_ACTIVITY_THREAD)
                                        for f in thread.frames)


def detect(text: str) -> Source | None:
    """Which producer wrote this, by whose signature its threads match.

    Counted rather than decided on the first hit: a header line or a frame can
    look like somebody's signature, and one accidental match should not choose
    the reader for the whole file.

    A Crashlytics event as an agent's tool returns it is a record with
    fields, told by them before anything is counted; its thread text alone
    is counted like the rest, and loses a tie to every dump.
    """
    if _event_data(text) is not None:
        return EVENT
    head = text.splitlines()[:600]
    scored = [(sum(1 for line in head if source.signature.match(line)), source)
              for source in (*SOURCES, EVENT)]
    best, source = max(scored, key=lambda pair: pair[0])
    return source if best else None


def _state(word: str) -> str:
    """One vocabulary for states three sources spell differently.

    Crashlytics writes them in the words a person would (`timed waiting`),
    Play Console capitalises those words (`Timed Waiting`), and ART writes
    them as its enum (`TimedWaiting`). Lower-casing, after splitting on the
    inner capitals, turns the last two into the first and leaves anything
    unforeseen readable rather than dropped.
    """
    if not word or word.islower():
        return word
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", word).lower()


def parse(text: str, source: Source | None = None) -> Report:
    """A dump into threads. Everything unreadable is kept, not dropped."""
    source = source or detect(text) or CRASHLYTICS
    if source is EVENT:
        return parse_event(text)
    head: dict[str, str] = {}
    threads: list[Thread] = []
    unread: list[tuple[int, str]] = []
    current: Thread | None = None
    process = ""
    entries = 1
    skipped = 0
    preamble = 0
    in_block = False
    load: list[tuple[float, str]] = []
    truncated = False
    listed: dict[str, int] = {}
    heaps: dict[str, dict[str, str]] = {}

    lines = text.splitlines()
    for number, raw in enumerate(lines, 1):
        line = raw.rstrip()
        if not line.strip():
            continue

        signature = source.signature.match(line)
        if signature:
            fields = {k: v or "" for k, v in signature.groupdict().items()}
            note = fields.pop("note", "")
            # A thread the runtime never attached has no state to print, and
            # `not attached` is the more useful thing to say than nothing.
            state = fields.pop("state", "") or fields.pop("detached", "")
            fields.pop("detached", None)
            current = Thread(state=_state(state), process=process, **fields)
            threads.append(current)
            in_block = False
            if source.lock_on_signature:
                held = source.lock.search(note)
                if held:
                    current.lock = Lock(**held.groupdict())
                elif note:
                    current.unread.append(note)
            continue

        if current is not None:
            if source.body_indented and not raw[:1].isspace():
                # ART indents everything belonging to a thread. What is not
                # indented is the next thread, the block's end, or the
                # runtime's summary — none of them this thread's business.
                current = None
            elif _read_body(line, current, source):
                continue

        # Outside a thread: the record's own scaffolding, then the header.
        block = _PROCESS.match(line)
        if block:
            current, process = None, ""
            in_block = True
            continue
        named = _CMDLINE.match(line)
        if named:
            current, process = None, named.group("cmd")
            continue
        if _ENTRY.match(line):
            # A second entry is a second ANR. Reading it into the same report
            # would merge two freezes into one set of threads.
            if threads:
                # Every entry left is counted, by the time and tag that open
                # it; a rule with no such line after it is still one more.
                entries = max(2, 1 + sum(1 for later in lines[number:]
                                         if _ENTRY_HEAD.match(later)))
                break
            # Everything before the first separator is what `dumpsys` says
            # about the drop box itself — how many entries it holds, what it
            # was asked for. None of it describes this ANR.
            head.clear()
            current = None
            continue
        busy = _LOAD.match(line)
        if busy:
            load.append((float(busy.group("share")),
                         busy.group("name").strip()))
            continue
        if _TRUNCATED.match(line):
            truncated = True
            continue
        count = _LISTED.match(line)
        if count:
            listed[process] = int(count.group("n"))
            continue
        if _SCAFFOLD.match(line):
            continue

        # The header runs before the first thread, and only before it. After
        # the threads begin, ART prints its own statistics in the same
        # `Key: value` shape — heap, intern table, JNI globals, loaded
        # libraries — and reading those as fields of the report puts the
        # runtime's bookkeeping where the application's identity belongs.
        if current is None:
            stat = _HEAP_LINE.match(line) if threads or in_block else None
            if stat:
                heaps.setdefault(process, {}).setdefault(stat["key"], stat["value"])
                continue
            if threads:
                # Everything after the threads: the runtime summary, the block
                # rules, the kernel wait channels. Unbounded, different on
                # every vendor's build, and none of it about this freeze.
                skipped += 1
                continue
            if in_block:
                # The same statistics before the first thread, where an older
                # ART prints them: `Build fingerprint:`, `Heap:`, `JNI:` are
                # the runtime's, not fields of this report.
                preamble += 1
                continue
            field_line = _HEADER.match(line)
            if field_line:
                head[field_line.group("key").strip()] = field_line.group("value").strip()
                continue
            # `# Crashlytics -` opens every export and carries nothing. A
            # commented line without a `key: value` in the header region is
            # decoration; counting it as unreadable would put a complaint on
            # every report this reader handles perfectly.
            if not line.startswith("#"):
                unread.append((number, line))
        else:
            current.unread.append(line)

    report = Report(head=head, threads=threads, source=source.name,
                    unread=unread, entries=entries, skipped=skipped, preamble=preamble,
                    load=sorted(load, reverse=True), truncated=truncated,
                    listed=listed, heaps=heaps)
    return _scoped(report)


def _read_body(line: str, thread: Thread, source: Source) -> bool:
    """A line inside a thread block, or False when it belongs to nobody."""
    frame = source.frame.match(line)
    if frame:
        thread.frames.append(frame.group("frame"))
        return True
    if source.native.match(line):
        thread.native.append(line.strip())
        if not thread.frames:
            thread.above += 1
        return True
    if _OMITTED.match(line):
        # The source stopped writing the stack here. Read as one more line
        # nothing understood, it was only a count at the end of the report,
        # and the thread was judged on a top with its bottom missing.
        thread.cut = len(thread.frames) + len(thread.native)
        return True
    if not source.lock_on_signature:
        denied = source.lock.match(line)
        if denied:
            thread.lock = Lock(**denied.groupdict())
            return True
        if source.held:
            holds = source.held.match(line)
            if holds:
                thread.held.append(holds.group("addr"))
                return True
    if source.noise and source.noise.match(line):
        return True
    if source.sys_tid:
        systid = source.sys_tid.match(line)
        if systid:
            thread.systid = systid.group("systid")
            return True
        # The rest of ART's `|` continuation lines are scheduler and stack
        # bookkeeping. Skipped by shape rather than listed: a build that adds
        # a field to them must not read as a format this cannot handle.
        if line.lstrip().startswith("|"):
            return True
    return False


def _scoped(report: Report) -> Report:
    """Threads of the process that hung, and a count of the ones set aside.

    An ANR trace holds a block per process — the app that froze first, then
    whatever else the system thought worth dumping. Reading them as one process
    would put another app's stalled thread in this app's findings.

    The block's `Cmd line:` also goes into the header when the header does not
    name the process. A trace pulled from `/data/anr/` has no header at all,
    and with one process in it the report was titled "unknown application"
    while the name was in the file.
    """
    seen = {t.process for t in report.threads if t.process}
    if not seen:
        return report
    target = report.package if report.package in seen else next(
        (t.process for t in report.threads if t.process), "")
    if len(seen) > 1:
        kept = [t for t in report.threads if t.process == target]
        report.elsewhere = len(report.threads) - len(kept)
        report.threads = kept
    if not report.head.get("Process"):
        report.head["Process"] = target
    return report


# --- an event from an agent's tool ------------------------------------------
#
# An agent reaches Crashlytics through a tool, not the console, and the tool
# gives an event rather than the export: YAML in the shape the Firebase tools
# print it, where most fields are themselves small YAML documents in a string
# (`issue: |` over `id:`, `title:`, `subtitle:`), or JSON in the shape the API
# returns it, where `threads` is a list of frames. Either is read here into the
# same threads a dump gives, and nothing is made up on the way: no state, no
# tid, no lock note, since the event has none.

# The fields only an event has. One of them, at the start of a line or as a
# key of a JSON document, is what makes a file worth parsing as one at all.
_EVENT_FIELDS = ("threads", "eventId", "bundleOrPackage", "buildStamp",
                 "processState", "blameFrame")
_EVENT_KEY = re.compile(
    r'^\s*(?:-\s+)?"?(?:' + "|".join(_EVENT_FIELDS) + r')"?\s*:', re.MULTILINE)
# `revision: "…"` inside `buildStamp`, which the tools give as the build
# system's own text and the API as a string or an object.
_REVISION = re.compile(r'\brevision"?\s*[:=]\s*"?(?P<rev>[0-9a-fA-F]{7,40})\b')
# One frame of an event's thread text: `at <symbol>`, and its place in
# parentheses after a space when it has one — `(MessageQueue.java:355)`,
# `(Native method)`. A native symbol's own parentheses follow it without a
# space, `__futex_wait_ex(void volatile*, …)`, and stay part of it.
_EVENT_FRAME = re.compile(r"^\s*at(?:\s+(?P<symbol>.*?))?(?:\s+\((?P<where>[^()]*)\))?\s*$")
# A managed frame's symbol: a dotted name and nothing else. What is not is a
# native frame — `__epoll_pwait`, `art::Thread::DumpState const`.
_JAVA_SYMBOL = re.compile(r"^[\w$]+(?:\.[\w$<>-]+)+$")


def _event_data(text: str) -> list[dict[str, Any]] | None:
    """The events in this file, or None when it is not one.

    Parsed only when it looks like one: a dump of three hundred threads is
    not handed to a YAML parser on the chance. A file that parses and holds
    no event — a list of strings, an export's text — is not one either.
    """
    stripped = text.lstrip()
    if not (stripped[:1] in ("{", "[") or _EVENT_KEY.search(text)):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        import yaml   # echolot's own dependency; only an event needs it here
        try:
            data = yaml.safe_load(text)
        except (yaml.YAMLError, RecursionError):
            return None
    if isinstance(data, dict) and isinstance(data.get("events"), list):
        data = data["events"]
    items = data if isinstance(data, list) else [data]
    found = [e for e in items if isinstance(e, dict)
             and any(k in e for k in _EVENT_FIELDS)]
    return found or None


def _field(event: dict[str, Any], key: str) -> Any:
    """A field of the event, unwrapped when the tool wrote it as YAML text.

    The Firebase tools print most fields as a small YAML document of their
    own inside a string: `issue` arrives as the text `id: …\\ntitle: …`, and
    `eventTime` as `'2026-…'` with its quotes. What parses into an object is
    that object; a scalar stays the text it was, without the quotes.
    """
    value = event.get(key)
    if not isinstance(value, str):
        return value
    import yaml
    try:
        inner = yaml.safe_load(value)
    except (yaml.YAMLError, RecursionError):
        return value.strip()
    if isinstance(inner, (dict, list)):
        return inner
    return inner.strip() if isinstance(inner, str) else value.strip()


def _text(value: Any) -> str:
    """A field as one line of text, empty for what is not a scalar."""
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _add_frame(thread: Thread, symbol: str, where: str | None) -> None:
    """One frame of an event, written the way a dump writes it.

    A managed frame becomes `pkg.Class.method(File.kt:12)`, the shape every
    rule after this reads, and one without a place says `(Unknown Source)`,
    as ART does. A native frame keeps its symbol and is counted above the
    managed ones while none has come yet, as a native unwinder's dump is.
    """
    if _JAVA_SYMBOL.match(symbol):
        thread.frames.append(f"{symbol}({where or 'Unknown Source'})")
        return
    thread.native.append(symbol + (f" ({where})" if where else ""))
    if not thread.frames:
        thread.above += 1


def _event_threads(text: str) -> tuple[list[Thread], list[tuple[int, str]]]:
    """The threads of an event's thread text, and the lines outside them."""
    threads: list[Thread] = []
    unread: list[tuple[int, str]] = []
    current: Thread | None = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        named = EVENT.signature.match(line)
        if named:
            current = Thread(name=named.group("name"), state="")
            threads.append(current)
            continue
        if current is None:
            unread.append((number, line))
            continue
        if _OMITTED.match(line):
            # Where the tool stopped writing the stack, as in an export.
            current.cut = len(current.frames) + len(current.native)
            continue
        frame = _EVENT_FRAME.match(line)
        if frame and frame.group("symbol"):
            _add_frame(current, frame.group("symbol"), frame.group("where"))
        else:
            current.unread.append(line)
    return threads, unread


def _json_threads(items: list[Any]) -> list[Thread]:
    """The threads of an event in the API's shape: a list of frame objects."""
    threads = []
    for item in items:
        if not isinstance(item, dict):
            continue
        thread = Thread(name=_text(item.get("name") or item.get("threadName")), state="")
        frames = item.get("frames")
        for frame in frames if isinstance(frames, list) else []:
            if isinstance(frame, str):
                read = _EVENT_FRAME.match(frame if frame.lstrip().startswith("at")
                                          else "at " + frame)
                if read and read.group("symbol"):
                    _add_frame(thread, read.group("symbol"), read.group("where"))
                continue
            if not isinstance(frame, dict) or not _text(frame.get("symbol")):
                continue
            file, line = _text(frame.get("file")), _text(frame.get("line"))
            where = f"{file}:{line}" if file and line not in ("", "0") \
                and file != "Native method" else file
            _add_frame(thread, _text(frame["symbol"]), where or None)
        threads.append(thread)
    return threads


def parse_event(text: str) -> Report:
    """A Crashlytics event, as an agent's tool returns it, into a report.

    The first event when the file holds several — what a batch call gives —
    with the rest counted, as a second ANR in a drop box record is. The
    thread text on its own, as an agent might paste it out of the tool's
    answer, is read as an event with nothing else in it.
    """
    events = _event_data(text)
    if events is None:
        threads, unread = _event_threads(text)
        return Report(head={}, threads=threads, source=EVENT.name,
                      unread=unread, event=Event())
    data = events[0]
    raw = data.get("threads")
    threads = (_event_threads(raw)[0] if isinstance(raw, str)
               else _json_threads(raw) if isinstance(raw, list) else [])

    issue = _field(data, "issue")
    issue = issue if isinstance(issue, dict) else {}
    version = _field(data, "version")
    if isinstance(version, dict):
        version = _text(version.get("displayName")) or " ".join(
            x for x in (_text(version.get("displayVersion")),
                        f"({_text(version.get('buildVersion'))})"
                        if _text(version.get("buildVersion")) else "") if x)
    stamp = data.get("buildStamp")
    revision = _REVISION.search(stamp if isinstance(stamp, str)
                                else json.dumps(stamp, default=str))
    keys = _field(data, "customKeys")
    state = _text(_field(data, "processState"))

    head = {key: value for key, value in (
        ("Application", _text(_field(data, "bundleOrPackage"))),
        ("Version", _text(version)),
        ("Issue", _text(issue.get("id"))),
        ("Date", _text(_field(data, "eventTime"))),
        ("Event", _text(_field(data, "eventId"))),
    ) if value}
    event = Event(
        title=_text(issue.get("title")),
        subtitle=_text(issue.get("subtitle")),
        process_state="" if state.endswith("UNSPECIFIED") else state,
        revision=revision.group("rev").lower() if revision else "",
        custom_keys=tuple(str(k) for k in keys) if isinstance(keys, dict) else (),
    )
    return Report(head=head, threads=threads, source=EVENT.name,
                  entries=len(events), event=event)


def checkout_revision(root: Path) -> str | None:
    """The commit `root` is checked out at; None outside git, or with no git."""
    import subprocess
    try:
        done = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    head = done.stdout.strip()
    return head if done.returncode == 0 and head else None


def is_the_build(report: Report) -> bool | None:
    """Whether the checkout is at the revision the event's build was made
    from; None when either is unknown."""
    revision = report.event.revision if report.event is not None else ""
    if not revision or not report.checkout:
        return None
    return report.checkout.lower().startswith(revision)


# --- a minified build -------------------------------------------------------
#
# The device's own record of a minified build says `a.b.run(SourceFile:3)`,
# and so does an export from a console that was never given the build's
# mapping. Nothing in such a frame is in the checkout, its package reads as
# somebody else's, and a coroutine worker parked in `c.b$a.run` is not seen
# as idle. So the frames are named back first, and everything after reads
# the source's names.


def retrace(report: Report, mapping: Path) -> None:
    """Every frame R8 wrote, and every monitor's class, named back by the build's mapping.

    As R8's own `retrace` does it: by the line, out through the methods
    inlined there, so one frame can become several. The number in a frame is
    taken whatever file it names — `SourceFile`, `r8-map-id-…`, and
    `unavailable`, where ART prints an instruction's offset for a build R8
    left without a line table, which is what that build's mapping is written
    in. A frame that arrived unminified, from a console that had the mapping,
    is not R8's and stays as it was (see `mapping._ours`). The frames are
    retraced together, as one build's (`mapping.retrace_frames`): when they
    name their files after a mapping that is not this one, nothing is.
    """
    parsed: dict[str, tuple[str, str, str | None, int | None]] = {}
    for thread in report.threads:
        for text in thread.frames:
            m = _FRAME.match(text)
            if m is None or text in parsed:
                continue
            cls, _, method = m.group("symbol").strip().rpartition(".")
            if cls:
                parsed[text] = (cls, method, *mapping_mod.where(m.group("where")))
    monitors = {t.lock.cls for t in report.threads if t.lock}
    batch = mapping_mod.retrace_frames(mapping, parsed, also=monitors)
    if batch.foreign:
        report.retraced = {"frames": 0, "outside": 0,
                           "map_id": {"frames": batch.foreign[0], "mapping": batch.foreign[1]}}
        return
    named = {text: _as_lines(got, parsed[text][2]) for text, got in batch.traced.items()}
    for text, got in batch.traced.items():
        if len(got.chains) == 1:
            report.guessed |= {line for frame, line in zip(got.frames, named[text], strict=True)
                               if frame.guessed}
        elif got.chains[0][0].guessed:
            report.guessed |= set(named[text])
    for thread in report.threads:
        thread.frames = [new for text in thread.frames for new in named.get(text, [text])]
        if thread.lock:
            thread.lock.cls = batch.retracer.real(thread.lock.cls) or thread.lock.cls
    report._chains = None
    report.retraced = {"frames": len(named), "outside": batch.outside}
    if batch.matched:
        report.retraced["matched_map_id"] = batch.matched


def _as_lines(traced: mapping_mod.Retraced, file: str | None) -> list[str]:
    """A retraced frame as the lines of a dump: one per method inlined there.

    A frame that could be several methods stays one line naming each, as a
    sampled frame does, `pkg.Disk.checksum | checksumLegacy(Disk.kt)`: the
    package reads off it as off any other. A native method keeps saying so.
    """
    if len(traced.chains) == 1:
        if file == "Native method":
            return [f"{frame.symbol}(Native method)" for frame in traced.frames]
        return [frame.text() for frame in traced.frames]
    first = traced.chains[0][0]
    names = [first.symbol] + [chain[0].method if chain[0].cls == first.cls
                              else chain[0].symbol for chain in traced.chains[1:]]
    return [" | ".join(dict.fromkeys(names)) + f"({first.file})"]


def minified_note(report: Report) -> str:
    """The frames still in R8's names, how many and the first, and what names
    them back; empty when there are none.

    Read after "What": `anr` lists it among what the file cannot say, and
    `mark --from-anr` says it in place of an empty plan's sentence, which
    described the manifest's mode and read as code with nothing to mark
    (#730).
    """
    left = _minified(report)
    if not left:
        return ""
    if report.retraced is None:
        return (f"{len(left)} frames are: they read as R8 named them, first "
                f"`{left[0]}`. The build's `mapping.txt` names them back: "
                f"`--mapping`, or `project.mapping` in the echolot.yml under "
                f"`--root`.")
    return (f"{len(left)} frames are: they still read as R8 named them after "
            f"the mapping, first `{left[0]}`. Code that arrived minified, as "
            f"some SDKs ship, is in no mapping of this build, and one from "
            f"another build misses the app's own.")


def _minified(report: Report) -> list[str]:
    """The frames worth reading that still read as R8 named them, first seen first."""
    seen: dict[str, None] = {}
    for thread in of_interest(report):
        for text in thread.frames:
            m = _FRAME.match(text)
            if m and mapping_mod.minified(m.group("symbol").strip()):
                seen.setdefault(text)
    return list(seen)


# --- what "doing nothing" looks like ----------------------------------------
#
# Every entry earned its place on a real report: without the first five, ten
# dumps from a live app left 27 threads parked in `Unsafe.park` and 25 in
# `Object.wait` in the list of suspects. The list grows as reports arrive, and
# that is the shape of the work rather than a defect — each entry is a claim
# that a stack means idleness, and each is worth a test.
#
# Direction of error matters: a family missing from here shows a thread that
# was doing nothing, which costs a reader a glance. A family wrongly listed
# hides a thread that was working, which costs the investigation. So a mask
# goes in only when the frame below it can mean nothing else.
IDLE: list[tuple[str, tuple[str, ...]]] = [
    ("looper idle", (
        "android.os.MessageQueue.nativePollOnce",
    )),
    # `.take()` on a blocking queue is the one shape here that means only one
    # thing: the thread has no work and is waiting to be given some. Listed by
    # queue class rather than by the `Unsafe.park` above them all — a parked
    # thread is not idle by itself, and on the sample reports the thread
    # holding the lock the whole app was queued behind was parked too.
    ("pool waiting for work", (
        "java.util.concurrent.ThreadPoolExecutor.getTask",
        "java.util.concurrent.LinkedBlockingQueue.take",
        "java.util.concurrent.PriorityBlockingQueue.take",
        "java.util.concurrent.ArrayBlockingQueue.take",
        "ScheduledThreadPoolExecutor$DelayedWorkQueue.take",
        "java.util.concurrent.SynchronousQueue.poll",
    )),
    ("coroutine worker parked", (
        "CoroutineScheduler$Worker.park",
        "CoroutineScheduler$Worker.tryPark",
    )),
    ("runtime daemon", (
        "java.lang.Daemons$",
    )),
    ("binder pool waiting", (
        "IPCThreadState::joinThreadPool",
    )),
    ("runtime housekeeping", (
        "ThreadPoolWorker::Run",
        "ProfileSaver::RunProfileSaverThread",
        "TaskProcessor::RunAllTasks",
        "SignalCatcher::Run",
        "walSyncThreadFunc",
        "libperfetto_hprof.so",
    )),
    ("library idle loop", (
        "java.util.TimerThread.mainLoop",
        "com.google.android.gms.dynamite.zza.run",
        "okhttp3.internal.concurrent.TaskRunner",
        # The frame that waits, not the watchdog's `run` under it: a watchdog
        # that fired closes the socket from `timedOut()` with `run` still on
        # the stack, and every frame of that is a library's, so a mask on
        # `run` would strike out the one moment it did something. The
        # substring covers the platform's copy, `com.android.okhttp.okio`.
        "okio.AsyncTimeout.awaitTimeout",
        "okio.AsyncTimeout$Companion.awaitTimeout",
    )),
    ("waiting on a descriptor", (
        "__epoll_pwait",
        "__ppoll",
        "java.nio.channels.Selector.select",
    )),
    ("sleeping", (
        "__futex_wait",
        "pthread_cond_wait",
        "java.lang.Thread.sleep",
    )),
]

# A call that is under way, read off the managed frames. `__ppoll` under them
# is a socket read in progress rather than a loop waiting for events: a TLS
# handshake, a request waiting for its response. Such a thread waits for the
# network on somebody's behalf, and when the main thread is queued behind a
# lock it holds, it is the thread a reader is sent to.
#
# Not `SocketInputStream.socketRead0` on its own: OkHttp's HTTP/2 reader
# (`Http2Reader.nextFrame` under `Http2Connection$ReaderRunnable`) waits in
# the same read for as long as a connection is open, and it is idle.
CALL_IN_PROGRESS = (
    "okhttp3.internal.connection.RealCall.",
    "okhttp3.RealCall.",
    "okhttp3.internal.connection.ConnectPlan.",
    "okhttp3.internal.connection.RealConnection.connect",
    "okhttp3.internal.http1.Http1ExchangeCodec.",
    "com.android.okhttp.internal.huc.HttpURLConnectionImpl.",
    "ConscryptEngineSocket.startHandshake",
    "ConscryptFileDescriptorSocket.startHandshake",
    "java.net.Socket.connect",
)

# A looper waiting for a message, seen the way a native unwinder writes it:
# `MessageQueue.next` (or `nextLegacy`) is the top managed frame, and above it
# sit only the native half of the poll, ART's JNI entry and exit, and the
# futex the runtime waits in to re-enter managed code. The managed
# `nativePollOnce` that the looper mask reads is not there.
_LOOPER_NEXT = ("android.os.MessageQueue.next(", "android.os.MessageQueue.nextLegacy(")
_LOOPER_NATIVE = (
    "epoll_pwait", "epoll_wait", "Looper::pollOnce", "Looper::pollInner",
    "android_os_MessageQueue_nativePollOnce",
    "art_jni_trampoline", "art_jni_method_end", "artJniMethodEnd",
    "ConditionVariable::WaitHoldingLocks", "syscall", "__futex_wait",
)


def _looper_wait(thread: Thread) -> bool:
    """Whether the thread waits in its looper with nothing of its own above."""
    if not thread.frames or not thread.frames[0].startswith(_LOOPER_NEXT):
        return False
    return all(any(part in line for part in _LOOPER_NATIVE)
               for line in thread.native[thread.dumping:thread.above])


# The runtime's thread that writes the dump is runnable in every dump it
# writes. That is a property of its stack: this reader does not know which
# signal the dump answered.
_WRITING_THE_DUMP = ("SignalCatcher::HandleSigQuit", "DumpForSigQuit")


def idle_reason(thread: Thread, ours: Ownership | None = None) -> str | None:
    """Why this thread was doing nothing, or None if it was working.

    A blocked thread is never idle whatever its stack says: it was denied a
    monitor, which is the thing this module exists to find. Nor is a
    runnable one: it was running. The main thread is idle only at its
    looper; a queue's `take` under `onCreate` is the app waiting for
    something, on the one thread that must not.

    A mask says idle only for what sits under it. Work above it — a frame of
    the project's, or `Binder.execTransact` serving a call — means the loop
    was running something: the app's task above `TimerThread.mainLoop`, its
    `finalize()` above the finalizer daemon, a provider's `query` on a
    binder thread. Struck out, each of those was missing from the working
    threads the reader is sent to for a holder. A native mask has every
    managed frame above it, since managed code runs called from native.

    A call in progress is work too (see `CALL_IN_PROGRESS`), and a stack the
    source cut short is never struck out by a native mask: that mask judges
    by every managed frame, and the ones below the cut may be the app's. A
    managed mask judges by the frames above itself, which a cut below does
    not take away.
    """
    if thread.state == "runnable" and any(
            mark in line for line in thread.native for mark in _WRITING_THE_DUMP):
        return "runtime housekeeping"
    if thread.state in ("blocked", "runnable"):
        return None
    ours = ours or Ownership()
    if _looper_wait(thread):
        return "looper idle"
    main = is_main(thread)

    def busy(frames: list[str]) -> bool:
        return any(ours.claims(f) or "Binder.execTransact" in f
                   or any(call in f for call in CALL_IN_PROGRESS) for f in frames)

    for reason, masks in IDLE:
        if main and reason != "looper idle":
            continue
        for i, frame in enumerate(thread.frames):
            if any(mask in frame for mask in masks):
                if not busy(thread.frames[:i]):
                    return reason
                break
        else:
            if any(mask in line for line in thread.own_native for mask in masks) \
                    and thread.cut is None and not busy(thread.frames):
                return reason
    return None


def working(report: Report) -> list[Thread]:
    """The threads left after the idle ones are struck out."""
    ours = report.ownership
    return [t for t in report.threads if idle_reason(t, ours) is None]


# --- a garbage collection ---------------------------------------------------
#
# A thread that allocates into a full heap stops until the collector has made
# room, and ART shows it in its own native frames above the managed frame that
# allocated: `Heap::WaitForGcToCompleteLocked` under `AllocateInternalWithGc`
# when another collection is running, `Heap::CollectGarbageInternal` when the
# thread runs one itself. The device's record says it in the state as well,
# `WaitingForGcToComplete` and `WaitingPerformingGc`.
#
# The managed frame under them is where the thread ran into the collection,
# and rarely why: a collection is made necessary by everything the app
# allocates and keeps, on every thread. A main thread stopped there inside
# `Choreographer.doFrame` was reported as standing on the platform, with "no
# line of yours to open" — the one sentence that sent the reader away from
# the app's own allocations.
_GC_WAITS = ("art::gc::Heap::WaitForGcToComplete",)
_GC_RUNS = ("art::gc::Heap::CollectGarbageInternal",)
_GC_STATES = {"waiting for gc to complete": False, "waiting performing gc": True}
_ALLOCATING = ("art::gc::Heap::AllocateInternalWithGc",
               "art::gc::Heap::AllocObjectWithAllocator",
               "artAlloc", "art_quick_alloc")
# A collection the code asked for by name. The call is then the line to open,
# not the heap: `Runtime.gc` is what `System.gc()` reaches.
_EXPLICIT_GC = ("java.lang.Runtime.nativeGc(", "java.lang.Runtime.gc(",
                "java.lang.System.gc(")
_HEAP_SYMBOL = re.compile(r"art::gc::Heap::\w+")


@dataclass(frozen=True)
class GcWait:
    """A thread stopped for a garbage collection.

    `frame` is the runtime's frame that says so (`art::gc::Heap::…`), empty
    when only the state does. `running` is whether the thread ran the
    collection itself rather than waited for another's. `cause` is
    `allocation` when an allocation asked for it, `explicit` when a
    `Runtime.gc` call did, and None when the frames do not say.
    """
    frame: str
    running: bool
    cause: str | None


def gc_wait(thread: Thread) -> GcWait | None:
    """Whether this thread was stopped for a garbage collection, and how.

    Read off the native frames above its top managed one — the ones it was
    standing in — and off the state ART wrote. Nothing managed decides it: a
    main thread asleep in its own code, or denied a monitor, is not this.
    """
    above = thread.native[thread.dumping:thread.above]
    waits = next((line for line in above if any(m in line for m in _GC_WAITS)), "")
    runs = next((line for line in above if any(m in line for m in _GC_RUNS)), "")
    by_state = _GC_STATES.get(thread.state)
    if not (waits or runs) and by_state is None:
        return None
    found = _HEAP_SYMBOL.search(waits or runs)
    running = bool(runs and not waits) if waits or runs else bool(by_state)
    if any(f.startswith(_EXPLICIT_GC) for f in thread.frames[:3]) or \
            any("Runtime_gc" in line or "Runtime_nativeGc" in line for line in above):
        cause: str | None = "explicit"
    elif any(m in line for line in above for m in _ALLOCATING):
        cause = "allocation"
    else:
        cause = None
    return GcWait(found.group(0) if found else "", running, cause)


def gc_waiters(report: Report) -> list[Thread]:
    """The working threads other than the main one stopped for a collection.

    Each was allocating when it stopped, which is the closest a dump comes to
    saying who allocates at that moment. The collector's own daemon is struck
    out as idle before this, and is not one of them.
    """
    return [t for t in working(report)
            if t is not report.main and gc_wait(t) is not None]


def main_gc(report: Report) -> GcWait | None:
    """The main thread's collection; never one denied a monitor, whose
    holder is the finding."""
    main = report.main
    return gc_wait(main) if main is not None and not main.lock else None


def heap_bound(report: Report) -> GcWait | None:
    """The main thread's collection, when the heap made it necessary — not
    one a `Runtime.gc` call asked for, which is a line to open."""
    gc = main_gc(report)
    return gc if gc is not None and gc.cause != "explicit" else None


# --- the lock chain ---------------------------------------------------------

@dataclass
class Chain:
    monitor: str                 # the class the source printed; the waiters' when inferred
    named: str | None            # the same class, unobfuscated, when derivable
    waiters: list[Thread]
    # Every thread between the waiters and the bottom: the one holding this
    # monitor first, then whoever is holding what that one wants, and so on.
    # Usually one long. When it is longer, the last is the only one worth
    # reading — the others are queued exactly like the waiters are.
    holders: list[Thread] = field(default_factory=list)
    # The holders that wait on one another, when the walk down them came back
    # to a thread it had passed: a deadlock. None of them is standing on
    # anything of its own, so a chain with one has no root.
    loop: list[Thread] = field(default_factory=list)
    # One of the waiters is in that loop: the holder waits, directly or not,
    # on a thread queued for this very monitor. Without it, the holders block
    # each other and the waiters queue behind them.
    cycle: bool = False
    # The monitor was worked out from the waiters rather than read off a note:
    # no thread in the dump carried one. A source that records no ownership
    # never does, and others can come without any.
    inferred: bool = False

    @property
    def owner(self) -> Thread | None:
        """Who holds this monitor. Not necessarily who is to blame."""
        return self.holders[0] if self.holders else None

    @property
    def root(self) -> Thread | None:
        """The thread at the bottom, waiting on nothing anyone here holds.

        This is the one standing on the actual blocking call. Reporting the
        direct holder as the cause when it is itself queued behind someone
        else names a victim. A deadlock has none: the last holder walked is
        waiting on one passed already, and naming it called a blocked
        thread — once the chain's own waiter — the one standing on its own.
        """
        return self.holders[-1] if self.holders and not self.loop else None

    @property
    def blocks_main(self) -> bool:
        return any(is_main(t) for t in self.waiters)


# What R8 makes of a class name: a letter or three, lower case, with a digit
# now and then. A class that kept its name starts with a capital.
_R8_NAME = re.compile(r"^[a-z][a-z0-9]{0,2}$")


def monitor_class(monitor: str, waiters: list[Thread]) -> str | None:
    """The unobfuscated name of the monitor, from the waiters' own frames.

    Crashlytics unminifies the frames and leaves the class inside the signature
    as R8 wrote it: a thread blocked on `ru.example.data.t` shows
    `ru.example.data.StateRepository.getCurrentState` as its own top frame. A
    blocked thread is standing in the method it could not enter, so its top
    frame names the class whose monitor it wants — no mapping file needed.
    Verified on both chains in the sample reports.

    The inference holds only while the two agree on the package, and that is
    what decides it. A waiter blocked inside a library on a monitor belonging
    to the app would otherwise rename the finding after the library.

    And only for a name R8 wrote: a few lower-case letters where a class name
    would be. `synchronized(cache)` inside `Repository.get` locks a `Cache`
    that kept its name, and the waiter's top frame, `Repository`, shares its
    package; renamed after it, the lock section and the main thread section
    named one lock two ways.
    """
    where, _, simple = monitor.rpartition(".")
    if not where:
        return None
    if not _R8_NAME.match(simple):
        return monitor
    for thread in waiters:
        if not thread.frames:
            continue
        named = thread.frames[0].split("(")[0].rsplit(".", 1)[0]
        if named.rsplit(".", 1)[0] == where:
            return named
    return None


def chains(report: Report) -> list[Chain]:
    """Every monitor somebody was denied, with who was holding it.

    Grouped by monitor and owner rather than by waiter: five threads denied the
    same lock by the same thread are one finding, and listing them as five
    would bury the one that matters — whether `main` is among them.

    Worked out once and kept on the report. It is a pure function of the
    threads, and one `echolot anr` asks for it several times: `locate` and
    the text or the JSON view each reach it, some more than once, and so does
    the command itself. On a three-hundred-thread dump each pass walks every
    thread and then walks the holders down to the root.
    """
    if report._chains is not None:
        return report._chains
    report._chains = _find_chains(report)
    return report._chains


def _holder_of(report: Report) -> Callable[[Lock], Thread | None]:
    """Who holds a lock: by the tid its note named, and when the note named
    none, or a tid the dump does not carry, by whoever says it holds that
    address. ART prints what a thread has locked; that is the second way to
    the same answer, and it survives an owner outside the process block that
    was read and a note ART could not finish."""
    by_tid = report.by_tid()
    by_addr: dict[str, Thread] = {}
    for t in report.threads:
        for addr in t.held:
            by_addr.setdefault(addr, t)
    return lambda lock: by_tid.get(lock.owner) or by_addr.get(lock.addr)


def _find_chains(report: Report) -> list[Chain]:
    holder = _holder_of(report)
    grouped: dict[tuple[str, str], list[Thread]] = {}
    for thread in report.threads:
        if thread.lock:
            grouped.setdefault((thread.lock.addr, thread.lock.owner), []).append(thread)

    found = []
    for waiters in grouped.values():
        lock = waiters[0].lock
        holders = _down_to_the_root(holder(lock), holder)
        loop = _loop(holders, holder)
        found.append(Chain(
            monitor=lock.cls,
            named=monitor_class(lock.cls, waiters),
            waiters=waiters,
            holders=holders,
            loop=loop,
            cycle=any(t is w for t in loop for w in waiters),
        ))
    if not found:
        found = _queues_without_a_note(report)
    # Main first, then by how many threads piled up behind the lock.
    return sorted(found, key=lambda c: (not c.blocks_main, -len(c.waiters)))


def _queues_without_a_note(report: Report) -> list[Chain]:
    """Queues read off the waiters, for a source that records no ownership.

    Play Console strips `held by thread N`, so an export from there says a
    thread is blocked and never says by whom — the strongest finding this
    reader has, gone. Most of it comes back without the note: a blocked thread
    is standing in the method it could not enter, so several of them standing
    in the same class are queued on the same monitor. The class rather than
    the method, because two synchronized methods of one object take the same
    lock — `read` and `write` behind one monitor is one queue. On a live
    export seven threads including main sat in one `getCurrentState`, and the
    Crashlytics report of the same freeze confirms that class is the monitor.

    What cannot be recovered is who holds it. That is said rather than guessed:
    the holder is somewhere among the threads that were working, and picking
    one would be inventing the half this source withheld.

    One blocked thread on its own is not a queue and is only worth a row when
    it is the main thread — everything else stops when that one does.
    """
    queues: dict[str, list[Thread]] = {}
    for thread in report.threads:
        if thread.state == "blocked" and thread.frames:
            owner = thread.frames[0].split("(")[0].strip().rsplit(".", 1)[0]
            if owner:
                queues.setdefault(owner, []).append(thread)
    return [
        Chain(monitor=where, named=None, waiters=waiting, holders=[],
              inferred=True)
        for where, waiting in queues.items()
        if len(waiting) > 1 or any(is_main(t) for t in waiting)
    ]


def _no_owner(report: Report) -> str:
    """Why a queue read off the waiters has no holder, in this source's terms.

    Only Play Console strips ownership. A Crashlytics export or an ART dump
    records it, and one that carried no lock note at all was told the source
    does not, which sent the reader to export the same freeze from elsewhere.
    """
    if report.source == PLAY.name:
        return ("This source does not record who holds a monitor, so the "
                "holder is not in this file at all. It is one of the threads "
                "below that were working.")
    return ("No thread in this dump carries a note on the monitor it wants, so "
            "who holds it is not in this file. It is one of the threads below "
            "that were working.")


def _down_to_the_root(owner: Thread | None,
                      holder: Callable[[Lock], Thread | None]) -> list[Thread]:
    """The holder, then whoever is holding what the holder wants, to the end.

    A holder that is itself blocked is a link, not a cause. Naming it as the
    answer sends a reader to a thread that is queued exactly like the ones
    behind it, and the thread actually standing on the blocking call is
    further down.

    Stops on a thread that wants nothing, on one this dump does not carry, and
    on a repeat — a cycle is a deadlock, and it is also the only way this could
    walk forever.
    """
    walked: list[Thread] = []
    current = owner
    while current is not None and not any(current is t for t in walked):
        walked.append(current)
        if current.lock is None:
            break
        current = holder(current.lock)
    return walked


def _loop(holders: list[Thread],
          holder: Callable[[Lock], Thread | None]) -> list[Thread]:
    """The holders that wait on one another, when the walk ended on a repeat.

    From the thread the last one waits on to the end of the walk. A loop the
    waiters are not in is still a deadlock: the holders block each other, and
    the waiters queue behind them.
    """
    last = holders[-1] if holders else None
    if last is None or last.lock is None:
        return []
    nxt = holder(last.lock)
    for i, t in enumerate(holders):
        if t is nxt:
            return holders[i:]
    return []


# --- from a frame to a file -------------------------------------------------
#
# A java frame carries its own source location: the compiler wrote the file
# name and the line into it, and Crashlytics gives them back unminified. So
# nothing has to be searched for — a basename and a package are enough to point
# at a file in the repository, and that is a shorter road than the one
# `domains` takes from a slice name.
#
# The walk is `domains.source_files`, not `mark`'s. That direction is the one
# that matters: `mark` will want to read an ANR report, so a module this one
# imports must not be one that imports it. `domains` imports nothing of ours.

# `pkg.Class.method(File.kt:58)`, and everything that is not that:
#   (Native method)                              nothing to point at
#   (unavailable:0)                              the frame the runtime lost
#   (com.google.x:artifact@@1.2.3:2)             a dependency's coordinate
_FRAME = re.compile(r"^(?P<symbol>[^(]+)\((?P<where>[^)]*)\)\s*$")
_WHERE = re.compile(r"^(?P<file>[\w$]+\.(?:kt|java))(?::(?P<line>\d+))?$")


@dataclass
class Located:
    frame: str
    symbol: str      # pkg.Class.method
    file: str        # relative to the root
    line: int | None
    exact: bool      # not a guess: one candidate, or the package agreed
    # The other files it could be, when it is a guess: the same name, and as
    # far as the package says, the same place.
    others: tuple[str, ...] = ()
    # The file the frame named, when it was placed in another: a Kotlin
    # class's frame that says `Guard.java`, placed in `Guard.kt`.
    named: str = ""
    # What the line is in now, when that is not the function the frame
    # names: "line 138 is now inside `clearAll`, the frame names `await`".
    # Empty when they agree, and when there is no line to check.
    astray: str = ""
    # A line past the end of the file, in a file that declares the function
    # the frame names: Kotlin's numbering of an `inline fun` copied into it.
    # `at` is the declaration it is placed at, and `inlined` says so. Not a
    # sign of another build, and not counted with `astray`.
    at: int | None = None
    inlined: str = ""


def source_index(root: Path) -> dict[str, list[Path]]:
    """Every Kotlin and Java source under the root, by file name.

    The walk is `domains.source_files`. Borrowing it is safe in the direction
    that matters: `domains` imports nothing of ours, so this does not become
    the cycle the note above is about — `mark` importing `anr`.
    """
    found: dict[str, list[Path]] = {}
    for path in source_files(root):
        found.setdefault(path.name, []).append(path)
    return found


# `package com.example.app` — Kotlin with no semicolon, Java with one. Only
# comments and file annotations may come before it, so the head of a file is
# enough to find it. The head is decoded as `utf-8-sig`: a byte-order mark an
# editor left on the first line would otherwise keep it from the `^`.
_PACKAGE = re.compile(r"^[ \t]*package[ \t]+(?P<name>[\w.]+)", re.MULTILINE)


def declared_packages(index: dict[str, list[Path]]) -> frozenset[str]:
    """Every package a source file in the index says it is in.

    Read off the `package` line rather than the directory. Kotlin lets a file
    live in a directory that does not spell its package out, and the line is
    what the compiler went by — so it is what the frame says too.
    """
    found: set[str] = set()
    for paths in index.values():
        for path in paths:
            try:
                with path.open("rb") as source:
                    head = source.read(8192).decode("utf-8-sig", errors="replace")
            except OSError:
                continue
            named = _PACKAGE.search(head)
            if named:
                found.add(named.group("name"))
    return frozenset(found)


def place(frame: str, index: dict[str, list[Path]], root: Path) -> Located | None:
    """Where in the repository this frame is, or None when it says nowhere.

    The file name alone is ambiguous in a multi-module project — two modules
    holding `Mapper.kt` is ordinary. The package from the frame's own symbol
    settles it, and when it settles nothing the first candidate is returned
    with `exact` false rather than silently picked as if it were certain.
    """
    parsed = _FRAME.match(frame)
    if not parsed:
        return None
    where = _WHERE.match(parsed.group("where").strip())
    if not where:
        return None
    symbol = parsed.group("symbol").strip()
    # `pkg.Class$1.onClick` — the anonymous class is still that package.
    package = _package_of(symbol)
    wanted = "/" + package.replace(".", "/") if package else ""

    named = where.group("file")
    candidates = index.get(named)
    if not candidates:
        found = _kotlin_for(symbol, named, package, index, root)
        if not found:
            return None
        line = where.group("line")
        return Located(frame=frame, symbol=symbol,
                       file=found[0].relative_to(root).as_posix(),
                       line=int(line) if line else None, exact=len(found) == 1,
                       others=tuple(p.relative_to(root).as_posix() for p in found[1:]),
                       named=named)

    # One file of that name in the whole checkout is not a guess, whatever the
    # package says: Kotlin lets a class live in a directory that does not spell
    # out its package, and warning about that would cry wolf on every one.
    #
    # Where the package does settle it, the directory has to end in it, from
    # one `/` to the file. As a plain substring `com/example/a` is inside
    # `com/example/app`, and `com.example.a.Mapper` was placed, as certain, in
    # the other package's `Mapper.kt` with its own sitting in the next module.
    #
    # Several can end in it: a class kept in both `src/debug` and
    # `src/release` of one module. The first of them in sorted order is a
    # guess like any other, and it was marked exact, so a release frame was
    # placed in the debug file without a word.
    agree = [path for path in candidates if wanted and
             ("/" + path.relative_to(root).parent.as_posix()).endswith(wanted)]
    pool = agree or candidates
    chosen, exact = pool[0], len(pool) == 1
    line = where.group("line")
    return Located(frame=frame, symbol=symbol,
                   file=chosen.relative_to(root).as_posix(),
                   line=int(line) if line else None, exact=exact,
                   others=() if exact else tuple(
                       p.relative_to(root).as_posix() for p in pool[1:]))


def _class_of(symbol: str) -> str:
    """`Guard` of `pkg.Guard$run$1.invoke`: the outer class the frame is in."""
    return symbol.rsplit(".", 1)[0].rsplit(".", 1)[-1].split("$", 1)[0]


def _in_package(path: Path, root: Path, package: str) -> bool:
    """Whether a source file is in this package: by its directory, or by the
    `package` line, which is what the compiler went by."""
    wanted = "/" + package.replace(".", "/") if package else ""
    if wanted and ("/" + path.relative_to(root).parent.as_posix()).endswith(wanted):
        return True
    try:
        with path.open("rb") as source:
            head = source.read(8192).decode("utf-8-sig", errors="replace")
    except OSError:
        return False
    declared = _PACKAGE.search(head)
    return (declared.group("name") if declared else "") == package


def _kotlin_for(symbol: str, named: str, package: str,
                index: dict[str, list[Path]], root: Path) -> list[Path]:
    """The Kotlin files a frame naming `X.java` the checkout does not have is in.

    A deobfuscated stack can name the file after the class with `.java`,
    Kotlin classes included — where the mapping kept no source file name,
    as far as the events show. The name follows the build, not the class:
    one frame read `(X.kt:N)` in one release and `(X.java:N)` in the next.
    Looked up by the name alone, such a frame was in no file, and `mark`
    counted it as a sign of another build on the build that froze.

    `X.kt` of the frame's package first; then the file of a top-level
    function's `XKt`; then a `.kt` file in the package's directory that
    declares the class — `FooImpl` declared in `Foo.kt`.
    """
    if not named.endswith(".java"):
        return []
    cls = _class_of(symbol)
    names = [named[:-len(".java")] + ".kt"]
    if cls.endswith("Kt") and len(cls) > 2:
        names.append(cls[:-2] + ".kt")
    for name in names:
        found = [p for p in index.get(name, []) if _in_package(p, root, package)]
        if found:
            return found
    if not cls or not package:
        return []
    declares = re.compile(
        r"^[ \t]*(?:[\w@]+[ \t]+)*(?:class|object|interface)[ \t]+"
        + re.escape(cls) + r"\b", re.MULTILINE)
    wanted = "/" + package.replace(".", "/")
    found = []
    for name, paths in index.items():
        if not name.endswith(".kt"):
            continue
        for path in paths:
            if not ("/" + path.relative_to(root).parent.as_posix()).endswith(wanted):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if declares.search(text) and _in_package(path, root, package):
                found.append(path)
    return sorted(found)


def of_interest(report: Report) -> list[Thread]:
    """The threads a reader is shown, and therefore the ones worth placing.

    Every thread in the dump would be hundreds of frames of platform and
    library code with nothing of the project in them.
    """
    seen: dict[str, Thread] = {}
    for chain in chains(report):
        for thread in [*chain.waiters, chain.owner, *chain.loop]:
            if thread is not None:
                seen[f"{thread.name}/{thread.tid}"] = thread
    for thread in [report.main, *working(report)]:
        if thread is not None:
            seen.setdefault(f"{thread.name}/{thread.tid}", thread)
    return list(seen.values())


def locate(report: Report, root: Path,
           index: dict[str, list[Path]] | None = None) -> tuple[list[Located], list[str]]:
    """Frames of this project placed in the repository, and those left over.

    The leftovers are the point of returning two lists. A frame this project
    owns that the repository cannot place means one of two things — the module
    is not in this checkout, or the report is from a version that no longer
    matches it — and both are worth saying rather than rounding down to a
    shorter list.

    Which frames the project owns is the checkout's to say, so this is where
    it is asked. What its sources declare is kept on the report, and every
    section `render` prints afterwards draws the line in the same place: a
    stack that showed `DoubleCheck.get` as the app's own, next to a placement
    that had set it aside, would be two readings of one file.
    """
    index = source_index(root) if index is None else index
    report.declared = declared_packages(index) or None
    if report.event is not None and report.event.revision:
        # An event names the commit its build was made from, which a dump
        # never does: whether this checkout is that commit is then a fact to
        # state, not a guess from lines that moved (#525).
        report.checkout = checkout_revision(root)
    if not index:
        # No sources: no frame of the report is missing from them.
        return [], []
    ours = report.ownership
    placed: list[Located] = []
    missing: list[str] = []
    seen: set[str] = set()
    for thread in of_interest(report):
        for frame in thread.own(ours):
            if frame in seen:
                continue
            seen.add(frame)
            found = place(frame, index, root)
            if found:
                if frame in report.guessed:
                    # The frame's `X.java` is echolot's guess, not the
                    # build's: a checkout with `X.kt` does not disagree.
                    found.named = ""
                placed.append(found)
                continue
            # Matched once and kept. The same regex ran twice here — once to
            # ask whether the frame parses and once to read the group out of
            # it — which is a second pass over every frame this checkout could
            # not place, and a shape that reads as if the two calls could
            # disagree.
            named = _FRAME.match(frame)
            if named and _WHERE.match(named.group("where").strip()):
                # It named a source file, and this checkout has no such file.
                missing.append(frame)
    _check_functions(placed, root)
    return placed, missing


def _check_functions(placed: list[Located], root: Path) -> None:
    """Whether each placed line is inside the function its frame names.

    The file and the line are the frame's own, and the file is the right
    one; the line is the build's. On a checkout nine releases ahead of the
    report, five of twelve placed frames fell inside another function, and
    the report printed them as the lines to open. `mark` has asked this of
    every frame all along; `anr` asks the same, the same way.

    A frame whose line is in no function, beside a frame of the same method
    whose line is in it, is the compiler's bridge and not a stale line.
    """
    from . import mark as mark_mod   # mark imports nothing of anr's

    texts: dict[str, str | None] = {}
    for found in placed:
        if found.line is None:
            continue
        if found.file not in texts:
            try:
                texts[found.file] = mark_mod.read_source(root / found.file)
            except OSError:
                texts[found.file] = None
        text = texts[found.file]
        if text is None:
            continue
        suffix = Path(found.file).suffix
        declared = mark_mod.declared_functions(text, suffix)
        wanted = mark_mod.frame_function(found.symbol, declared)
        block = mark_mod.enclosing_block(text, suffix, found.line)
        if block is None:
            if mark_mod.expression_body(text, suffix, found.line, wanted) is not None:
                continue
            decls = mark_mod.inlined_into(text, suffix, found.line, wanted)
            if decls:
                # Past the end of the file, in a file that declares the
                # function: inlined code, on the build that froze.
                found.at = decls[0] if len(decls) == 1 else None
                found.inlined = mark_mod.inlined_note(found.line, wanted, len(decls))
            else:
                found.astray = f"line {found.line} is in no function now"
        elif block[0] == wanted:
            continue
        elif mark_mod.lambda_frame(found.symbol) and wanted not in declared:
            # A lambda the compiler made into a class of its own, on the
            # build that froze as on any other.
            continue
        elif wanted not in declared:
            found.astray = f"the file declares no `{wanted}`"
        else:
            found.astray = (f"line {found.line} is now inside `{block[0]}`, "
                            f"the frame names `{wanted}`")
    agree = {(f.symbol, f.file) for f in placed if f.line is not None and not f.astray}
    for found in placed:
        if found.astray.endswith("in no function now") and (found.symbol, found.file) in agree:
            found.astray = ""


def astray(placed: list[Located], report: Report) -> int | None:
    """How many placed frames land outside their function, when that is most
    of them and says the checkout is not the build; None otherwise.

    Not after the build's mapping has named the frames back: R8 moves lines
    and inlines, and a retraced frame can sit inside its neighbour on the
    build that froze. `mark --from-anr` draws the line in the same place.
    """
    checked = [f for f in placed if f.line is not None]
    off = sum(1 for f in checked if f.astray)
    if report.retraced and report.retraced.get("frames"):
        return None
    return off if checked and off * 2 > len(checked) else None


# --- the report -------------------------------------------------------------

def _stack(thread: Thread, ours: Ownership, limit: int = 8) -> list[str]:
    """The frames worth printing: the top, the boundary, then the app's own.

    Three kinds of frame carry anything. The top says what the thread was
    standing on, and it is almost always `Unsafe.park` or `Object.wait` — true
    and useless alone. The app's own frames say who asked for it. Between them
    sits the frame where the app called into the library, and that one names
    what the wait actually was: `Tasks.await` reads differently from
    `Http2Stream.takeHeaders`, and printing only the two ends loses it. Every
    frame in between is the library's own plumbing.
    """
    own = [f for f in thread.own(ours) if f != thread.top]
    lines = [thread.top]
    if own:
        edge = thread.frames.index(own[0])
        if edge > 0 and thread.frames[edge - 1] != thread.top:
            lines.append(thread.frames[edge - 1])

    # Both ends of the app's own frames, never just the first ones. An OkHttp
    # call runs through eight of this project's interceptors before it reaches
    # anything; keeping only the top of that leaves out the method that took
    # the lock, which is the one an investigation opens.
    room = limit - len(lines)
    if len(own) > room:
        keep = own[:max(room - 3, 1)] + ["…"] + own[-2:]
    else:
        keep = own
    if own:
        return lines + keep
    # Nothing of the app on this stack. The nearest library frame is then
    # the one that says what the thread was doing, and it goes under the
    # top; the native frames, where there are any, sit after it.
    near = thread.nearest(ours)
    if near != thread.top and near not in lines:
        lines.append(near)
    elif near == thread.top and not near.startswith(CORE):
        # The thread stands inside a library. The frame where the library was
        # entered, the last of its frames before the platform's resume, says
        # what it was asked to do: `Gson.fromJson` under the adapter's `read`.
        entered = 0
        while entered + 1 < len(thread.frames) and \
                not thread.frames[entered + 1].startswith(CORE):
            entered += 1
        if entered:
            lines.append(thread.frames[entered])
    if len(lines) == 1:
        # The platform's all the way down. A binder call on top says only
        # "binder"; the interface proxy under the plumbing says which call,
        # and the frame that made it says what for.
        lines += _binder_call(thread.frames)
    return lines + thread.own_native[1:3] if len(lines) == 1 else lines


# The plumbing of a binder call, above the frame that names it.
_BINDER = ("android.os.BinderProxy.", "android.os.Binder.", "android.os.Parcel.")


def _binder_call(frames: list[str]) -> list[str]:
    """What a thread standing in a binder call was calling, and who called it.

    `BinderProxy.transactNative` on top is true and says nothing more. The
    first AIDL proxy below it (`IJobCallback$Stub$Proxy.acknowledgeStopMessage`)
    names the call into the system, and the frame under that one
    (`JobServiceEngine$JobHandler.ackStopMessage`) says why it was made. With
    no proxy, the first frame below the plumbing.
    """
    if not frames or not frames[0].startswith(_BINDER):
        return []
    for i, frame in enumerate(frames):
        if "$Stub$Proxy." in frame:
            return frames[i:i + 2]
    below = next((i for i, f in enumerate(frames) if not f.startswith(_BINDER)), None)
    return frames[below:below + 1] if below is not None else []


# Platform frames that point at the app's own setup, though nothing of the
# app is on the stack: (the frame, what it says, where to read next). A lead,
# not a cause — the platform is a dead end, the setup that drives it is not.
_PLATFORM_LEADS = (
    ("android.app.job.JobServiceEngine$JobHandler.ackStopMessage",
     "was telling the system that a JobScheduler job had stopped "
     "(`acknowledgeStopMessage` runs after `onStopJob` returns), and the call "
     "into the system had not returned",
     "Which jobs the app runs, and how often it schedules and cancels them "
     "(its WorkManager or JobService setup), is where to read next."),
    ("android.app.job.JobServiceEngine$JobHandler.ackStartMessage",
     "was telling the system that a JobScheduler job had started "
     "(`acknowledgeStartMessage` runs after `onStartJob` returns), and the "
     "call into the system had not returned",
     "Which jobs the app runs, and how often it schedules and cancels them "
     "(its WorkManager or JobService setup), is where to read next."),
)


def _platform_leads(report: Report) -> list[tuple[str, str, str, str]]:
    """(thread, frame, what it says, where to read) for each known platform lead."""
    out = []
    for thread in of_interest(report):
        for frame in thread.frames:
            lead = next((entry for entry in _PLATFORM_LEADS
                         if frame.startswith(entry[0] + "(")), None)
            if lead:
                out.append((thread.name, frame, lead[1], lead[2]))
                break
    return out


def doing(report: Report, busy: list[Thread], found: list[Chain]) -> list[Thread]:
    """The working threads that are neither the main thread nor queued on a lock.

    A waiter is left out whether or not it carries a lock note: the waiters of
    a chain inferred from a Play Console export carry none, and listed here
    they stood where the report says the holder is.
    """
    queued = {id(t) for c in found for t in c.waiters}
    return [t for t in busy
            if t is not report.main and not t.lock and id(t) not in queued]


def _nearest_libraries(report: Report) -> list[tuple[str, str]]:
    """(thread, frame) for every thread of interest whose nearest frame is a library's.

    Only when the app itself is on no stack: with own frames in the file the
    library frames are plumbing, and this list would point away from them.

    Below the top, the nearest frame is outside the platform core by
    construction, and with nothing of the app's anywhere it is a library's,
    named in `LIBRARIES` or not. Once a checkout decides what is the
    project's, most libraries are ones the list has not heard of, and asking
    it here as well would drop them as leads.
    """
    ours = report.ownership
    if any(t.own(ours) for t in of_interest(report)):
        return []
    out = []
    for t in of_interest(report):
        near = t.nearest(ours)
        # Skipped only when the stack is the platform's all the way down. A
        # thread standing inside a library has that library as its top, and
        # skipping it for that dropped the one lead the file had.
        if t.frames and not near.startswith(CORE):
            out.append((t.name, near))
    return out


def _gc_said(gc: GcWait) -> str:
    """What the main thread was doing, said of a collection it stopped for."""
    where = f" (`{gc.frame}`)" if gc.frame else ""
    if gc.cause == "explicit":
        return (f"It was **in a garbage collection** that a call asked for "
                f"by name{where}: `Runtime.gc`, which `System.gc()` reaches. "
                f"That call is the line to open, not the heap.")
    doing = (f"It was **running a garbage collection** itself{where}"
             if gc.running else
             f"It was **waiting for a garbage collection** to finish{where}")
    if gc.cause == "allocation":
        doing += ", having run into it while allocating"
    return (doing + ". The frame below is where it met the collection, not "
            "why one was needed: that is how much the whole app allocates and "
            "keeps on the heap, on every thread.")


def _short(symbol: str) -> str:
    """`pkg.Class.method` as `Class.method` — the path below carries the rest."""
    parts = symbol.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else symbol


def _standing(thread: Thread) -> str:
    """The line over a thread's stack: its state, when the source gave one."""
    if thread.state:
        return f"State `{thread.state}`, standing on:"
    return "No state in this input; standing on:"


def _event_said(report: Report) -> list[str]:
    """What an event says that a dump does not, under the header.

    The partial input is said first, before any section reads as if the
    file held the whole process: an event with one worker thread in it read
    as if that worker were the whole story.
    """
    event = report.event
    if event is None:
        return []
    names =", ".join(f"`{t.name or '(unnamed)'}`" for t in report.threads[:4])
    many = (f"**{len(report.threads)} threads** ({names})" if len(report.threads) > 1
            else f"**one thread**, {names}" if report.threads else "no thread")
    out = [f"Read from a Crashlytics event as an agent's tool returns it: "
           f"{many}, with no state, tid or lock note — what that leaves out "
           f"is under \"What this report does not say\"."]
    if event.subtitle or event.title:
        # Crashlytics' own reading of the mechanism. Not this reader's
        # finding, and said as its: on a deadlock it is the one thing the
        # file knows about the lock.
        said = ([f"_{event.subtitle}_"] if event.subtitle else []) \
            + ([f"`{event.title}`"] if event.title else [])
        out.append("Crashlytics files it as: " + " — ".join(said))
    facts = []
    if event.process_state:
        facts.append(f"the app was in the **{event.process_state.lower()}**")
    if event.revision:
        facts.append(f"its build was made from revision `{event.revision[:12]}`")
    if facts:
        line = "; ".join(facts)
        out.append(line[0].upper() + line[1:] + ".")
    if event.custom_keys:
        shown = ", ".join(f"`{k}`" for k in event.custom_keys[:8])
        if len(event.custom_keys) > 8:
            shown += f" and {len(event.custom_keys) - 8} more"
        out.append(f"The app set {len(event.custom_keys)} custom key(s) on it: "
                   f"{shown}. Their values are the app's data and are not "
                   f"copied here — some name a user or a session; read the ones "
                   f"you need in the event itself.")
    return out


def _revision_said(report: Report) -> list[str]:
    """The event's revision against the checkout's, over the placed frames."""
    revision = report.event.revision if report.event is not None else ""
    if not revision:
        return []
    same = is_the_build(report)
    if same:
        return [f"This checkout is at the build's own revision, "
                f"`{revision[:12]}`: the lines below are the build's.", ""]
    if same is None:
        return [f"The event names the build's revision, `{revision[:12]}`; "
                f"this directory is not a git checkout to compare it with.", ""]
    return [f"The event names the build's revision, `{revision[:12]}`, and this "
            f"checkout is at `{(report.checkout or '')[:12]}`: a line placed "
            f"here may have moved since. `git worktree add --detach <dir> "
            f"{revision[:12]}` checks the build out, and `--root <dir>` places "
            f"the frames there.", ""]


def render(report: Report, code: tuple[list[Located], list[str]] | None = None) -> str:
    """The dump as a findings list — sections that have something to say."""
    out: list[str] = ["# ANR Report", ""]
    ours = report.ownership

    head = report.head
    title = " ".join(x for x in (report.package, head.get("Version", "")) if x)
    marks = [x for x in (head.get("Issue", "")[:8], head.get("Date", "")) if x]
    out.append(f"**{title or 'unknown application'}**"
               + (" · " + " · ".join(marks) if marks else ""))
    if report.reason:
        out.append(f"Fired for: **{report.reason}**")

    busy = working(report)
    idle = len(report.threads) - len(busy)
    counted = (f"Threads: **{len(report.threads)}** — {len(busy)} doing "
               f"something, {idle} idle")
    if report.elsewhere:
        counted += (f" · {report.elsewhere} from other processes in this "
                    f"record, not read")
    out.append(counted)
    if report.entries > 1 and report.event is not None:
        out.append(f"This file holds {report.entries} events. Everything below "
                   f"is the first of them; give `anr` each event in a file of "
                   f"its own.")
    elif report.entries > 1:
        out.append("This file holds more than one ANR. Everything below is "
                   "the first of them.")
    if report.event is not None:
        out.extend(_event_said(report))
    out.extend(_retrace_note(report.retraced))
    out.append("")

    found = chains(report)
    if found:
        note = ("_several threads standing in the same class are queued on "
                "the same monitor — the holder is what this source withheld_"
                if all(c.inferred for c in found) else
                "_a monitor held by a thread that is itself waiting is the "
                "mechanism, not a coincidence — this section needs no trace_")
        out += ["## What was holding the lock", "", note, ""]
        for chain in found:
            name = chain.named or chain.monitor
            # The raw name only when it says something the resolved one does
            # not: a monitor that was never obfuscated resolves to itself.
            also = (f" (`{chain.monitor}` in the dump)"
                    if chain.named and chain.named != chain.monitor else "")
            who = ", ".join(f"`{t.name}`" for t in chain.waiters[:4])
            if len(chain.waiters) > 4:
                who += f" and {len(chain.waiters) - 4} more"
            verb = "blocked inside" if chain.inferred else "denied"
            out.append(f"**{len(chain.waiters)} {verb} `{name}`**{also} — {who}")
            if chain.inferred:
                out += ["", _no_owner(report), ""]
                continue
            if not chain.holders:
                out += ["", "Held by a thread that is not in this dump.", ""]
                continue
            if chain.loop:
                # A deadlock has no thread standing on anything of its own,
                # so it is the threads of the loop that are shown, each on
                # what it was waiting in.
                ring = " → ".join(f"`{t.name}`" for t in [*chain.loop, chain.loop[0]])
                if chain.cycle:
                    out += ["", f"> The holder is itself waiting on one of them: "
                                f"this is a deadlock. {ring}, each waiting on "
                                f"the next.", ""]
                else:
                    out += ["", f"> The holders block each other, a deadlock: "
                                f"{ring}, each waiting on the next. The threads "
                                f"above queue behind it.", ""]
                for t in chain.loop:
                    out += [f"**{t.name}** (tid {t.tid}, {t.state}) was on:",
                            "", "```"] + _stack(t, ours) + ["```", ""]
                continue
            links, root = chain.holders, chain.root
            if len(links) == 1:
                out += ["", f"Held by **{root.name}** (tid {root.tid}, "
                            f"{root.state}), which was standing on:"]
            else:
                # Every link but the last is queued exactly like the threads
                # above it. Reporting the first as the cause names a victim.
                out += ["", "Held through "
                        + " → ".join(f"`{t.name}`" for t in links)
                        + ", each waiting on the next. Only the last is "
                          "standing on anything of its own:",
                        "", f"**{root.name}** (tid {root.tid}, {root.state}) "
                            f"was on:"]
            out += ["", "```"] + _stack(root, ours) + ["```", ""]

    main = report.main
    gc = main_gc(report)
    # Stopped for a collection the heap made necessary, rather than one a
    # call asked for: the verdict and the gaps then point at what the app
    # allocates, which no frame here shows.
    heap = heap_bound(report) is not None
    if main is not None:
        out += ["## The main thread", ""]
        if main.name != "main":
            # The name the file gave it, kept: it is what a reader searches
            # the export for.
            out += [f"**{main.name}** is the main thread: it runs "
                    f"`ActivityThread.main`.", ""]
        reason = idle_reason(main, ours)
        if reason == "looper idle":
            out += ["It was **idle**, waiting for a message. Whatever caused "
                    "this ANR had already let go by the time the dump was "
                    "taken, or never ran on this thread at all — the frames "
                    "below name Android's message queue and nothing of the "
                    "app.", ""]
        elif main.lock:
            out += [f"**Blocked**, denied `{main.lock.cls}` by tid "
                    f"{main.lock.owner}. The section above has the holder.", ""]
        elif gc is not None:
            out += [_gc_said(gc), "", _standing(main), ""]
        else:
            out += [_standing(main), ""]
        out += ["```"] + _stack(main, ours) + ["```", ""]
        if main.cut is not None:
            out += [f"The source cut this stack after {main.cut} frames.", ""]
        if heap:
            also = gc_waiters(report)
            if also:
                # Each of them was allocating when it stopped: the nearest a
                # dump comes to who allocates at that moment.
                names = ", ".join(f"`{t.name}`" for t in also[:4])
                if len(also) > 4:
                    names += f" and {len(also) - 4} more"
                out += [f"{len(also)} other thread(s) here stopped for a "
                        f"collection too — {names}, listed below with what "
                        f"they were doing. Each was allocating at that "
                        f"moment, and none is the cause on its own either.", ""]
            if report.heap:
                out += ["The runtime's own count in this record: "
                        + " · ".join(f"`{key}: {value}`"
                                     for key, value in report.heap.items())
                        + ".", ""]

    if report.threads and not any(t.own(ours) for t in of_interest(report)):
        # Said as narrowly as it is checked. "Not this project's code" would
        # need to know which packages are the project's, and an app published
        # as `ru.example.app` routinely ships modules under `com.example.*`.
        # Without a checkout what can be stated is that nothing here is
        # outside the platform and the libraries every app carries; with one,
        # that nothing here is in a package the checkout declares.
        out += ["## Every frame here belongs to the platform or a library", ""]
        if heap:
            # True of the frames and wrong as a verdict: what made the
            # collection necessary is the app's, and no frame shows it.
            out += ["Not one frame outside them in any thread worth reading, "
                    "and here that is not the end of the road. The main "
                    "thread was stopped for a garbage collection, and a "
                    "collection is made necessary by what the app allocates "
                    "and keeps on the heap — the app's own code, though no "
                    "frame here shows it. A dump is one moment and cannot "
                    "name those lines. A trace of the same scenario says how "
                    "often the collector ran and which threads it stopped "
                    "(`gc_pressure`); a heap dump says what the heap held.", ""]
        else:
            out += ["Not one frame outside them in any thread worth reading. "
                    "The freeze is inside something nobody here wrote, and "
                    "there is no line of yours to open — which is a finding, "
                    "and not the same as an empty report.", ""]
        cut = [t for t in of_interest(report) if t.cut is not None]
        if cut:
            # A stack cut short says nothing about its bottom, and the bottom
            # is where the app's frames would be.
            out += [f"Unless it is below a cut: the source cut {len(cut)} of "
                    f"these stacks short, first **{cut[0].name}** after "
                    f"{cut[0].cut} frames, and what called into them is not "
                    f"in the file.", ""]
        near = _nearest_libraries(report)
        if near:
            # A library is not a wall. The app configures WorkManager, chose
            # OkHttp's interceptors, scheduled the job: the frame names which
            # of those to look at, and that is a lead where "platform" is not.
            out += ["The platform is a dead end; a library the app drives is "
                    "not. The frames nearest to the app:", ""]
            out += [f"- `{frame}` on **{name}**" for name, frame in near]
            out += ["", "That names what the app set up — its WorkManager jobs, "
                        "its network client, its coroutine scopes — and that "
                        "setup is where to read next.", ""]
        for name, frame, says, then in _platform_leads(report):
            # No line of the app's, and still not the end of the road: the
            # platform frame names the app's own setup it was serving.
            out += [f"**{name}**, at `{frame}`, {says}. {then}", ""]

    others = doing(report, busy, found)
    if others:
        out += ["## Threads that were doing something", ""]
        # Threads running the app's own code first. The idle vocabulary will
        # never cover every library, and a thread with no frame of this project
        # anywhere in it is the one a reader can do least with — so it sinks
        # rather than being struck out on a guess.
        ranked = sorted(others, key=lambda t: (not t.own(ours),
                                               t.state.endswith("waiting")))
        for thread in ranked[:12]:
            where = thread.nearest(ours)
            # An event gives no state, and `()` would read as one.
            state = f" ({thread.state})" if thread.state else ""
            line = f"- **{thread.name}**{state} — `{where}`"
            if where != thread.top:
                # The top said what the thread was standing on; this says
                # what it was doing. Both, because they read differently.
                line += f" — on top: `{thread.top}`"
            if gc_wait(thread) is not None:
                # Its managed frames say where it allocated, not that it
                # was stopped there.
                line += " — stopped for a garbage collection"
            if thread.cut is not None:
                # What it was doing may be below the cut, and so may the
                # app's frame that would have said who asked for it.
                line += f" — stack cut after {thread.cut} frames"
            out.append(line)
        if len(ranked) > 12:
            out.append(f"- … and {len(ranked) - 12} more")
        out.append("")

    placed, missing = code or ([], [])
    if placed:
        out += ["## Where these frames are in this checkout", "",
                "_the file and the line are the frame's own; each line was "
                "checked against the function the frame names_", ""]
        out += _revision_said(report)
        off = astray(placed, report)
        if off is not None:
            version = report.head.get("Version", "")
            revision = report.event.revision if report.event is not None else ""
            out += [f"> ⚠️ {off} of the {sum(1 for f in placed if f.line is not None)} "
                    f"frames placed land outside the function they name: this "
                    f"checkout is probably not the build that froze"
                    + (f" ({version})" if version else "")
                    + ". Check that build out"
                    + (f", revision `{revision[:12]}`," if revision else "")
                    + " before opening these lines.", ""]
        for found in placed:
            mark = "" if found.exact else (
                f"  ← one of {len(found.others) + 1} of that name, also "
                + ", ".join(found.others[:3])
                + (" …" if len(found.others) > 3 else ""))
            line = found.at or found.line
            where = found.file + (f":{line}" if line else "")
            if found.named:
                # Placed by its class: said, so the line is not read as a
                # file of that name somebody could look for.
                mark += f"  ← the frame names `{found.named}`"
            if found.astray:
                mark += f"  ← {found.astray}"
            if found.inlined:
                mark += f"  ← {found.inlined}"
            out.append(f"- `{_short(found.symbol)}` — {where}{mark}")
        out.append("")

    if report.load:
        out += ["## What else the device was doing", "",
                "_the record's own CPU table, over the seconds around the "
                "freeze — a machine that was busy is a different story from "
                "an app that blocked itself_", ""]
        for share, name in report.load[:6]:
            # Up to the colon, as `Ownership.claims` stops at the dot:
            # `com.example.app:remote` is the app, `com.example.application`
            # is not.
            mine = "  ← this app" if report.package and (
                name == report.package or name.startswith(report.package + ":")) else ""
            out.append(f"- {share}% `{name}`{mine}")
        out.append("")

    gaps = _gaps(report, missing)
    if gaps:
        out += ["## What this report does not say", ""] + gaps + [""]

    return "\n".join(out).rstrip() + "\n"


def _retrace_note(retraced: dict[str, Any] | None) -> list[str]:
    """What the build's mapping made of the frames R8 wrote, under the header.

    A mapping from another build is told by the frames it has no place for,
    and the right one leaves a few: from two of them and a tenth of the
    frames, unless the frames carry the mapping's own id, the line is a
    warning (`mapping.another_build`).
    """
    if retraced is None:
        return []
    if retraced.get("map_id"):
        ids = retraced["map_id"]
        return [f"> ⚠️ The frames name their files after mapping "
                f"`{mapping_mod.short_id(ids['frames'])}`, and this one is "
                f"`{mapping_mod.short_id(ids['mapping'])}`: another build's. Its "
                f"names would be wrong, so the frames stay as they were "
                f"written. Pass the mapping of the build that froze."]
    total, outside = retraced["frames"], retraced["outside"]
    if not total:
        return ["The build's mapping was read, and no frame here was R8's."]
    matched = retraced.get("matched_map_id")
    if mapping_mod.another_build(total, outside, matched):
        return [f"> ⚠️ {outside} of the {total} frames R8 wrote have no place in "
                f"the mapping: no range of their method holds their line. A "
                f"mapping from another build names the frames it happens to "
                f"match, wrongly, and has no place for the rest: check that it "
                f"is this build's."]
    line = f"The build's mapping named back the {total} frames R8 wrote."
    if outside:
        line += " " + mapping_mod.no_place(outside, matched)
    return [line]


# Why a file has no reason in it, said of the file that was read. Only the
# device's own record carries a `Subject`, and the sentence used to say "a
# Crashlytics export" whatever the input was — while sending the reader to
# Play Console for the reason, which its export does not carry either.
_NO_REASON = {
    CRASHLYTICS.name: "A Crashlytics export carries no reason, component or "
                      "intent; the device's own record does",
    PLAY.name: "A Play Console export carries no reason, component or intent; "
               "the device's own record does",
    DUMPSYS.name: "This is ART's dump without the drop box's header — a trace "
                  "pulled from `/data/anr/` has no `Subject:` — and the drop "
                  "box entry for the same freeze has one",
    EVENT.name: "A Crashlytics event carries no reason, component or intent "
                "either; the device's own record does",
}


def _event_gap(report: Report) -> str:
    """What an event as the tools give it cannot show, and where the rest is.

    Said as one item, first: it is the limit every other section stands
    on. A deadlock named by Crashlytics itself is said by name, since the
    second thread of it is exactly what the input leaves out.
    """
    event = report.event
    names = ", ".join(f"`{t.name or '(unnamed)'}`" for t in report.threads[:4])
    held = (f"one thread, {names} — the one Crashlytics blamed —"
            if len(report.threads) == 1 else
            f"{len(report.threads)} threads, {names},")
    said = (f"- The other threads, their states and their locks. A Crashlytics "
            f"event as an agent's tool returns it carries {held} with no state, "
            f"no tid and no lock note, and its stacks are often cut. So none of "
            f"this can be read off it: who held the monitor a thread wanted, "
            f"what the threads it does not carry were doing, and what the other "
            f"side of a binder call was doing.")
    if event is not None and "deadlock" in event.subtitle.lower():
        said += (" Crashlytics files this one as a deadlock, and a deadlock is "
                 "two threads each holding what the other wants: the second "
                 "thread and both lock notes are in the full dump, not here.")
    return (said + " The full dump is the console's export of this event, from "
            "the issue page — every thread, with states and lock notes — or the "
            "device's own record, which has the reason as well: `adb shell "
            "dumpsys dropbox --print data_app_anr`. When the question is a "
            "lock, ask the human for one of them.")


def _gaps(report: Report, missing: list[str] | None = None) -> list[str]:
    """What could not be read or was never there.

    A tool that answers only what it can answer has to say which questions it
    skipped, or silence reads as a clean bill.
    """
    gaps = []
    if report.event is not None:
        gaps.append(_event_gap(report))
    # Cut at the drop box's size limit, the dump reads as complete and a
    # holder past the cut as a thread that was not in it.
    if report.cut is not None:
        gaps.append(
            "- The threads past the cut. The drop box cut this entry at its "
            "size limit" + (f", {report.cut} of the threads ART listed are not "
                            f"in it" if report.cut else "")
            + ", so a holder named as not in this dump may be one of them. "
              "`/data/anr/` keeps the whole trace when the device lets you "
              "read it.")
    if not report.reason:
        why = _NO_REASON.get(report.source, "This file carries no reason, "
                             "component or intent; the device's own record does")
        gaps.append(f"- Why the system fired the ANR. {why}: "
                    f"`adb shell dumpsys dropbox --print data_app_anr`.")
    # ART writes the note on every thread waiting for a monitor and every frame
    # holding one, so the device's own record without a single note says that
    # none was — which is not a gap, and pointing the reader at that same
    # record for the notes it already lacks was wrong.
    # An event never carries one, and its own item above says so.
    if report.threads and not report.lock_notes \
            and report.source not in (DUMPSYS.name, EVENT.name):
        gaps.append("- Who was holding what. No thread in this file carries a "
                    "lock note (`waiting to lock … held by`), so a monitor "
                    "chain cannot be read off it: an empty chain list here "
                    "means the file cannot say, not that nothing was blocked. "
                    "The device's own record (`dumpsys dropbox`) keeps the notes.")
    if report.threads and report.main is None and report.event is not None:
        # Crashlytics blamed a worker, and the event carries that worker
        # alone: read as it stood, the report made the worker the whole story.
        gaps.append("- What the main thread was doing. The event carries the "
                    "thread Crashlytics blamed, and it is not the main thread: "
                    "whether the main thread was waiting for it, or for "
                    "something else, the event cannot show. The console's "
                    "export of the same event has the main thread.")
    elif report.threads and report.main is None:
        gaps.append("- What the main thread was doing. No thread in this file "
                    "is named `main` or runs `ActivityThread.main`, so it is "
                    "not in it, and the threads below may be all the file has.")
    if heap_bound(report) is not None:
        # The question the report raises and cannot answer, with the two
        # records that can. "No line of yours to open" stood here before.
        whole = ("the console's export of the same event" if report.event is not None
                 else "the whole event")
        alone = (f" This file holds the main thread alone; {whole} "
                 "has the others, and those stopped for the same collection "
                 "were allocating at that moment." if len(report.threads) == 1
                 else "")
        gaps.append("- Whose allocations made the collection necessary. A "
                    "dump names the thread that ran into it, not the code "
                    "that filled the heap. Record the same scenario and read "
                    "`gc_pressure`, which needs `atrace:dalvik`: how often the "
                    "collector ran and which threads it stopped. A heap dump "
                    "of the app in the same state says what the heap held." + alone)
        if not report.heap:
            where = ("`/data/anr/` keeps the whole trace when the device lets "
                     "you read it" if report.source == DUMPSYS.name else
                     "the device's own record of the same freeze has them: "
                     "`adb shell dumpsys dropbox --print data_app_anr`")
            gaps.append("- How full the heap was. The runtime writes its "
                        "`Heap:` line and its collection totals beside the "
                        f"threads, and this file has none of them; {where}.")
    gaps.append("- How long anything took. A dump is one moment; durations "
                "come from a trace.")
    if report.unread:
        first = report.unread[0]
        gaps.append(f"- {len(report.unread)} line(s) nothing here could read, "
                    f"first at line {first[0]}: `{first[1][:60]}`")
    stray = sum(len(t.unread) for t in report.threads)
    if stray:
        gaps.append(f"- {stray} line(s) inside thread blocks that are neither "
                    f"frames nor a lock note.")
    still = minified_note(report)
    if still:
        gaps.append(f"- What {still}")
    if missing:
        first = missing[0]
        named = _FRAME.match(first)
        file = _WHERE.match(named.group("where").strip()).group("file") if named else ""
        symbol = named.group("symbol").strip() if named else first
        gaps.append(f"- Where {len(missing)} of this project's frames are. The "
                    f"first, `{first}`, names `{file}`, a file this checkout "
                    f"does not have, and no file of it declares a class "
                    f"`{_class_of(symbol)}` in `{_package_of(symbol)}`. Either "
                    f"the module lives outside this checkout, or the class is "
                    f"not in it any more.")
    if report.skipped or report.preamble:
        where = ("before and after the threads" if report.skipped and report.preamble
                 else "after the threads" if report.skipped else "before the threads")
        gaps.append(f"- {report.skipped + report.preamble} line(s) {where} — the "
                    f"runtime's own statistics and the record's furniture — "
                    f"passed over by position rather than by name.")
    return gaps


def summary(report: Report,
            code: tuple[list[Located], list[str]] | None = None) -> dict[str, object]:
    """The same findings in the shape an agent walks.

    Two readers, one set of numbers — the Marker Report is built the same way,
    and for the same reason: a person reads the markdown, an agent reads this,
    and neither is given a different answer.
    """
    busy = working(report)
    main_thread = report.main
    ours = report.ownership
    gc = main_gc(report)
    return {
        "schema": 1,
        "source": report.source,
        "application": report.package,
        "reason": report.reason or None,
        "head": report.head,
        "threads": {
            "total": len(report.threads),
            "working": len(busy),
            "idle": len(report.threads) - len(busy),
            "other_processes": report.elsewhere,
        },
        "entries": report.entries,
        # Threads the drop box cut off: ART's count less the ones read, 0
        # when the cut fell after the last of them; null when not cut.
        "cut": report.cut,
        "chains": [
            {
                "monitor": chain.monitor,
                "named": chain.named,
                "blocks_main": chain.blocks_main,
                "inferred": chain.inferred,
                # The threads that wait on one another, when there is a loop;
                # `cycle` says a waiter is one of them.
                "deadlock": [t.name for t in chain.loop],
                "cycle": chain.cycle,
                "waiters": [t.name for t in chain.waiters],
                "holders": [t.name for t in chain.holders],
                "root": None if chain.root is None else {
                    "name": chain.root.name,
                    "tid": chain.root.tid,
                    "state": chain.root.state,
                    "stack": _stack(chain.root, ours),
                },
            }
            for chain in chains(report)
        ],
        "main": None if main_thread is None else {
            "name": main_thread.name,
            "state": main_thread.state,
            "idle": idle_reason(main_thread, ours),
            "denied": None if not main_thread.lock else main_thread.lock.cls,
            "stack": _stack(main_thread, ours),
            "cut": main_thread.cut,
            # What it was stopped for when it was neither idle nor denied a
            # monitor: "gc" for a garbage collection, else null. What to do
            # next is in `gc.cause`: a collection an allocation asked for
            # is the heap's, one `Runtime.gc` asked for is that call's.
            "waiting_for": None if gc is None else "gc",
            # The runtime's frame that says so (null when only the state
            # does), whether the thread ran the collection itself, and
            # "allocation", "explicit" or null for what asked for it.
            "gc": None if gc is None else {
                "frame": gc.frame or None,
                "running": gc.running,
                "cause": gc.cause,
            },
        },
        # `where` is the frame nearest to the app — its own, else a library's
        # — and `top` what the thread stood on; `stack` is the same cut the
        # markdown prints. The first version kept the top alone, which for a
        # working thread is the least informative frame it has.
        "working": [
            {"name": t.name, "state": t.state,
             "where": t.nearest(ours),
             "top": t.top,
             "stack": _stack(t, ours),
             # Frames read before the source cut the stack; null when whole.
             "cut": t.cut,
             # "gc" when it was stopped for a garbage collection, as `main`.
             "waiting_for": "gc" if gc_wait(t) is not None else None}
            for t in doing(report, busy, chains(report))
        ],
        # The runtime's own heap lines from the device's record, as it wrote
        # them: `Heap`, `Max memory`, the blocking collections. Empty for a
        # console export, which has none.
        "heap": report.heap,
        # Whether this file can name a lock chain at all — see `lock_notes`.
        "lock_notes": report.lock_notes,
        # Where the app's setup is reached when none of its own frames are:
        # a library it drives, per working thread.
        "nearest": [{"thread": name, "frame": frame}
                    for name, frame in _nearest_libraries(report)],
        # Platform frames that point at the app's own setup: what the thread
        # was doing there, and where to read next.
        "leads": [{"thread": name, "frame": frame, "says": says, "next": then}
                  for name, frame, says, then in _platform_leads(report)],
        "code": None if code is None else {
            "placed": [
                {"symbol": f.symbol, "file": f.file, "line": f.line,
                 "exact": f.exact, "others": list(f.others),
                 # The file the frame named when it was placed by its class.
                 "named": f.named or None,
                 # What the line is in now, when not the function named.
                 "astray": f.astray or None,
                 # A line past the end of the file: where the frame is
                 # placed, the declaration it names, and the note.
                 "at": f.at, "inlined": f.inlined or None}
                for f in code[0]
            ],
            "unplaced": code[1],
            # Most placed frames land outside their function: probably not
            # the build that froze.
            "not_the_build": astray(code[0], report) is not None,
            # The commit the checkout is at, read when the event names the
            # build's revision, and whether it is that revision; both null
            # when either is unknown.
            "checkout": report.checkout,
            "is_the_build": is_the_build(report),
        },
        # What a Crashlytics event says beside its threads; null for a dump.
        # `custom_keys` are names only: the values are the app's data.
        "event": None if report.event is None else {
            "title": report.event.title or None,
            "subtitle": report.event.subtitle or None,
            "process_state": report.event.process_state or None,
            "revision": report.event.revision or None,
            "custom_keys": list(report.event.custom_keys),
        },
        "load": [{"share": s, "process": n} for s, n in report.load],
        "own_frames": any(t.own(ours) for t in of_interest(report)),
        # What the build's mapping made of the frames R8 wrote: how many it
        # named back and how many it had no place for. Null without one.
        "retrace": report.retraced,
        # The frames worth reading that still read as R8 named them.
        "minified": len(_minified(report)),
        "unread": {
            "outside": len(report.unread),
            "inside": sum(len(t.unread) for t in report.threads),
            "after_the_threads": report.skipped,
            "before_the_threads": report.preamble,
        },
    }


def to_json(report: Report,
            code: tuple[list[Located], list[str]] | None = None) -> str:
    return json.dumps(summary(report, code), ensure_ascii=False, indent=2)
