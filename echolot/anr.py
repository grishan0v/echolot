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
# `dumpsys dropbox --print <tag>` concatenates every entry it holds.
_ENTRY = re.compile(r"^={20,}\s*$")
# The record's own furniture: block rules, the thread count above a block, and
# the runtime's note about how long suspending everything took. Skipped by
# name so that a line which is genuinely new still reaches the unread count.
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
    lock=re.compile(
        r"waiting to lock <(?P<addr>0x[0-9a-f]+)> "
        r"\((?P<cls>[^)]*)\) held by thread (?P<owner>-?\d+)"
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
    # with an article ART puts there: `(a java.lang.Object)`.
    lock=re.compile(
        r"^\s*- waiting to lock <(?P<addr>0x[0-9a-f]+)>"
        r"\s+\(a (?P<cls>[^)]*)\) held by thread (?P<owner>-?\d+)"
    ),
    # What a thread already holds. Crashlytics prints nothing of the kind, and
    # it is what lets the holder be found when `held by thread` is absent.
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

# Packages that belong to the platform, the runtime and the libraries every app
# carries. Used only to decide which frames are worth printing — a stack of
# twenty `java.util.concurrent` frames says nothing about the app that owns it.
# Getting this wrong hides a frame; it never invents one.
PLATFORM = (
    "java.", "javax.", "jdk.", "sun.", "libcore.", "dalvik.",
    "android.", "androidx.", "com.android.",
    "kotlin.", "kotlinx.",
    "com.google.", "okhttp3.", "okio.", "retrofit2.", "io.reactivex.",
)


@dataclass
class Lock:
    """The monitor a blocked thread wants, and who is holding it."""
    addr: str
    cls: str
    owner: str


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

    @property
    def top(self) -> str:
        """The frame this thread was standing on, java preferred."""
        if self.frames:
            return self.frames[0]
        return self.native[0] if self.native else "(no frames)"

    def own(self, prefixes: tuple[str, ...]) -> list[str]:
        """Frames that are neither platform nor a common library.

        An app whose own package sits under one of the platform roots would be
        struck out by the prefix rule alone, so its package wins over it.
        """
        return [f for f in self.frames
                if f.startswith(prefixes) or not f.startswith(PLATFORM)]


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
    # (share of a CPU, process name) from the record's own table, biggest
    # first. Empty for a source that does not print one.
    load: list[tuple[float, str]] = field(default_factory=list)
    # Lines after the threads that were passed over by position rather than by
    # name: the runtime's statistics and the record's own furniture.
    skipped: int = 0
    # What `chains()` worked out, kept once. Not part of the report's content
    # and not compared with anything: see `chains`.
    _chains: "list[Chain] | None" = field(default=None, repr=False, compare=False)

    @property
    def package(self) -> str:
        """Crashlytics names the application; the dropbox record names the
        process that hung, which is the same string for a single-process app
        and the right one to scope to when it is not."""
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
    def prefixes(self) -> tuple[str, ...]:
        """What counts as this project's own code, beyond the not-platform rule.

        Only needed for an app whose package sits under a platform root. Code
        under a second root of the same company — `ru.example.app` shipping
        modules as `com.example.*`, which is what the sample reports do — needs
        nothing here: it is not platform, so it survives on that alone.
        """
        pkg = self.package
        if not pkg:
            return ()
        parts = pkg.split(".")
        return (pkg,) if len(parts) < 2 else (pkg, ".".join(parts[:2]))

    def by_tid(self) -> dict[str, Thread]:
        """Threads by tid — the id `held by thread N` refers to.

        A thread the runtime never attached has no tid to be referred to by,
        and an empty key would collect all of them into one.
        """
        return {t.tid: t for t in self.threads if t.tid}

    @property
    def main(self) -> Thread | None:
        return next((t for t in self.threads if t.name == "main"), None)


def detect(text: str) -> Source | None:
    """Which producer wrote this, by whose signature its threads match.

    Counted rather than decided on the first hit: a header line or a frame can
    look like somebody's signature, and one accidental match should not choose
    the reader for the whole file.
    """
    head = text.splitlines()[:600]
    scored = [(sum(1 for line in head if source.signature.match(line)), source)
              for source in SOURCES]
    best, source = max(scored, key=lambda pair: pair[0])
    return source if best else None


def _state(word: str) -> str:
    """One vocabulary for states two sources spell differently.

    Crashlytics writes them in the words a person would (`timed waiting`), ART
    writes them as its enum (`TimedWaiting`). Splitting on the inner capitals
    turns the second into the first and leaves anything unforeseen readable
    rather than dropped.
    """
    if not word or word.islower():
        return word
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", word).lower()


def parse(text: str, source: Source | None = None) -> Report:
    """A dump into threads. Everything unreadable is kept, not dropped."""
    source = source or detect(text) or CRASHLYTICS
    head: dict[str, str] = {}
    threads: list[Thread] = []
    unread: list[tuple[int, str]] = []
    current: Thread | None = None
    process = ""
    entries = 1
    skipped = 0
    load: list[tuple[float, str]] = []

    for number, raw in enumerate(text.splitlines(), 1):
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
            continue
        named = _CMDLINE.match(line)
        if named:
            current, process = None, named.group("cmd")
            continue
        if _ENTRY.match(line):
            # A second entry is a second ANR. Reading it into the same report
            # would merge two freezes into one set of threads.
            if threads:
                entries += 1
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
        if _SCAFFOLD.match(line):
            continue

        # The header runs before the first thread, and only before it. After
        # the threads begin, ART prints its own statistics in the same
        # `Key: value` shape — heap, intern table, JNI globals, loaded
        # libraries — and reading those as fields of the report puts the
        # runtime's bookkeeping where the application's identity belongs.
        if current is None:
            if threads:
                # Everything after the threads: the runtime summary, the block
                # rules, the kernel wait channels. Unbounded, different on
                # every vendor's build, and none of it about this freeze.
                skipped += 1
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
                    unread=unread, entries=entries, skipped=skipped,
                    load=sorted(load, reverse=True))
    return _scoped(report)


def _read_body(line: str, thread: Thread, source: Source) -> bool:
    """A line inside a thread block, or False when it belongs to nobody."""
    frame = source.frame.match(line)
    if frame:
        thread.frames.append(frame.group("frame"))
        return True
    if source.native.match(line):
        thread.native.append(line.strip())
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
    """
    seen = {t.process for t in report.threads if t.process}
    if len(seen) <= 1:
        return report
    target = report.package if report.package in seen else next(
        (t.process for t in report.threads if t.process), "")
    kept = [t for t in report.threads if t.process == target]
    report.elsewhere = len(report.threads) - len(kept)
    report.threads = kept
    if not report.head.get("Process"):
        report.head["Process"] = target
    return report


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


def idle_reason(thread: Thread) -> str | None:
    """Why this thread was doing nothing, or None if it was working.

    A blocked thread is never idle whatever its stack says: it was denied a
    monitor, which is the thing this module exists to find.
    """
    if thread.state == "blocked":
        return None
    stack = "\n".join(thread.frames + thread.native)
    for reason, masks in IDLE:
        if any(mask in stack for mask in masks):
            return reason
    return None


def working(report: Report) -> list[Thread]:
    """The threads left after the idle ones are struck out."""
    return [t for t in report.threads if idle_reason(t) is None]


# --- the lock chain ---------------------------------------------------------

@dataclass
class Chain:
    monitor: str                 # the class as printed in the signature
    named: str | None            # the same class, unobfuscated, when derivable
    waiters: list[Thread]
    # Every thread between the waiters and the bottom: the one holding this
    # monitor first, then whoever is holding what that one wants, and so on.
    # Usually one long. When it is longer, the last is the only one worth
    # reading — the others are queued exactly like the waiters are.
    holders: list[Thread] = field(default_factory=list)
    cycle: bool = False          # a holder waits, directly or not, on a waiter
    # The monitor was worked out from the waiters rather than read off a note.
    # True only for a source that records no ownership at all.
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
        else names a victim.
        """
        return self.holders[-1] if self.holders else None

    @property
    def blocks_main(self) -> bool:
        return any(t.name == "main" for t in self.waiters)


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
    """
    where = monitor.rsplit(".", 1)[0] if "." in monitor else ""
    if not where:
        return None
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
    threads, and one `echolot anr` asked for it six times: `locate` through
    `of_interest`, `render` for its own section and again through
    `of_interest`, `summary` twice the same way, and the command itself. On a
    three-hundred-thread dump each pass walks every thread and then walks the
    holders down to the root.
    """
    if report._chains is not None:
        return report._chains
    report._chains = _find_chains(report)
    return report._chains


def _find_chains(report: Report) -> list[Chain]:
    by_tid = report.by_tid()
    grouped: dict[tuple[str, str], list[Thread]] = {}
    for thread in report.threads:
        if thread.lock:
            grouped.setdefault((thread.lock.addr, thread.lock.owner), []).append(thread)

    found = []
    for (addr, owner_tid), waiters in grouped.items():
        # By the tid the source named, and when that tid is not in the dump, by
        # whoever says it holds this monitor. ART prints what a thread has
        # locked; that is the second way to the same answer, and it survives an
        # owner outside the process block that was read.
        owner = by_tid.get(owner_tid) or next(
            (t for t in report.threads if addr in t.held), None)
        monitor = waiters[0].lock.cls if waiters[0].lock else ""
        found.append(Chain(
            monitor=monitor,
            named=monitor_class(monitor, waiters),
            waiters=waiters,
            holders=_down_to_the_root(owner, by_tid),
            cycle=_waits_back(owner, {t.tid for t in waiters}, by_tid),
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
    in the same one are queued on the same monitor. On a live export seven
    threads including main sat in one `getCurrentState`, and the Crashlytics
    report of the same freeze confirms that class is the monitor.

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
        if len(waiting) > 1 or any(t.name == "main" for t in waiting)
    ]


def _down_to_the_root(owner: Thread | None,
                      by_tid: dict[str, Thread]) -> list[Thread]:
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
    seen: set[str] = set()
    current = owner
    while current is not None and current.tid not in seen:
        seen.add(current.tid)
        walked.append(current)
        if current.lock is None:
            break
        current = by_tid.get(current.lock.owner)
    return walked


def _waits_back(owner: Thread | None, waiters: set[str], by_tid: dict[str, Thread]) -> bool:
    """Does the owner wait, directly or through others, on one of its waiters?

    That is a deadlock, and it is also the only way the walk below could run
    forever. Both reasons to answer it before printing anything.
    """
    seen: set[str] = set()
    current = owner
    while current is not None and current.lock is not None:
        if current.tid in seen:
            return True
        seen.add(current.tid)
        if current.lock.owner in waiters:
            return True
        current = by_tid.get(current.lock.owner)
    return False


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
    candidates = index.get(where.group("file"))
    if not candidates:
        return None

    symbol = parsed.group("symbol").strip()
    # `pkg.Class$1.onClick` — the anonymous class is still that package.
    owner = symbol.rsplit(".", 1)[0].split("$", 1)[0]
    package = owner.rsplit(".", 1)[0] if "." in owner else ""
    wanted = "/".join(package.split(".")) if package else ""

    # One file of that name in the whole checkout is not a guess, whatever the
    # package says: Kotlin lets a class live in a directory that does not spell
    # out its package, and warning about that would cry wolf on every one.
    chosen, exact = candidates[0], len(candidates) == 1
    for path in candidates:
        if wanted and wanted in path.as_posix():
            chosen, exact = path, True
            break
    line = where.group("line")
    return Located(frame=frame, symbol=symbol,
                   file=chosen.relative_to(root).as_posix(),
                   line=int(line) if line else None, exact=exact)


def of_interest(report: Report) -> list[Thread]:
    """The threads a reader is shown, and therefore the ones worth placing.

    Every thread in the dump would be hundreds of frames of platform and
    library code with nothing of the project in them.
    """
    seen: dict[str, Thread] = {}
    for chain in chains(report):
        for thread in [*chain.waiters, chain.owner]:
            if thread is not None:
                seen[f"{thread.name}/{thread.tid}"] = thread
    for thread in [report.main, *working(report)]:
        if thread is not None:
            seen.setdefault(f"{thread.name}/{thread.tid}", thread)
    return list(seen.values())


def locate(report: Report, root: Path) -> tuple[list[Located], list[str]]:
    """Frames of this project placed in the repository, and those left over.

    The leftovers are the point of returning two lists. A frame this project
    owns that the repository cannot place means one of two things — the module
    is not in this checkout, or the report is from a version that no longer
    matches it — and both are worth saying rather than rounding down to a
    shorter list.
    """
    index = source_index(root)
    placed: list[Located] = []
    missing: list[str] = []
    seen: set[str] = set()
    for thread in of_interest(report):
        for frame in thread.own(report.prefixes):
            if frame in seen:
                continue
            seen.add(frame)
            found = place(frame, index, root)
            if found:
                placed.append(found)
            elif _FRAME.match(frame) and _WHERE.match(
                    _FRAME.match(frame).group("where").strip()):
                # It named a source file, and this checkout has no such file.
                missing.append(frame)
    return placed, missing


# --- the report -------------------------------------------------------------

def _stack(thread: Thread, prefixes: tuple[str, ...], limit: int = 8) -> list[str]:
    """The frames worth printing: the top, the boundary, then the app's own.

    Three kinds of frame carry anything. The top says what the thread was
    standing on, and it is almost always `Unsafe.park` or `Object.wait` — true
    and useless alone. The app's own frames say who asked for it. Between them
    sits the frame where the app called into the library, and that one names
    what the wait actually was: `Tasks.await` reads differently from
    `Http2Stream.takeHeaders`, and printing only the two ends loses it. Every
    frame in between is the library's own plumbing.
    """
    own = [f for f in thread.own(prefixes) if f != thread.top]
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
    return lines + keep if own else (
        lines + thread.native[1:3] if len(lines) == 1 else lines)


def _short(symbol: str) -> str:
    """`pkg.Class.method` as `Class.method` — the path below carries the rest."""
    parts = symbol.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else symbol


def render(report: Report, code: tuple[list[Located], list[str]] | None = None) -> str:
    """The dump as a findings list — sections that have something to say."""
    out: list[str] = ["# ANR Report", ""]

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
    if report.entries > 1:
        out.append("This file holds more than one ANR. Everything below is "
                   "the first of them.")
    out.append("")

    found = chains(report)
    if found:
        note = ("_several threads standing in the same method are queued on "
                "the same monitor — the holder is what this source withheld_"
                if all(c.inferred for c in found) else
                "_a monitor held by a thread that is itself waiting is the "
                "mechanism, not a coincidence — this section needs no trace_")
        out += ["## What was holding the lock", "", note, ""]
        for chain in found:
            name = chain.named or chain.monitor
            also = f" (`{chain.monitor}` in the dump)" if chain.named else ""
            who = ", ".join(f"`{t.name}`" for t in chain.waiters[:4])
            if len(chain.waiters) > 4:
                who += f" and {len(chain.waiters) - 4} more"
            verb = "blocked inside" if chain.inferred else "denied"
            out.append(f"**{len(chain.waiters)} {verb} `{name}`**{also} — {who}")
            if chain.cycle:
                out.append("")
                out.append("> The holder is itself waiting on one of them: "
                           "this is a deadlock.")
            if chain.inferred:
                out += ["", "This source does not record who holds a monitor, "
                            "so the holder is not in this file at all. It is "
                            "one of the threads below that were working.", ""]
                continue
            if not chain.holders:
                out += ["", "Held by a thread that is not in this dump.", ""]
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
            out += ["", "```"] + _stack(root, report.prefixes) + ["```", ""]

    main = report.main
    if main is not None:
        out += ["## The main thread", ""]
        reason = idle_reason(main)
        if reason == "looper idle":
            out += ["It was **idle**, waiting for a message. Whatever caused "
                    "this ANR had already let go by the time the dump was "
                    "taken, or never ran on this thread at all — the frames "
                    "below name Android's message queue and nothing of the "
                    "app.", ""]
        elif main.lock:
            out += [f"**Blocked**, denied `{main.lock.cls}` by tid "
                    f"{main.lock.owner}. The section above has the holder.", ""]
        else:
            out += [f"State `{main.state}`, standing on:", ""]
        out += ["```"] + _stack(main, report.prefixes) + ["```", ""]

    if report.threads and not any(t.own(report.prefixes)
                                 for t in of_interest(report)):
        # Said as narrowly as it is checked. "Not this project's code" would
        # need to know which packages are the project's, and an app published
        # as `ru.example.app` routinely ships modules under `com.example.*`.
        # What can be stated without guessing is that nothing here is outside
        # the platform and the libraries every app carries.
        out += ["## Every frame here belongs to the platform or a library", "",
                "Not one frame outside them in any thread worth reading. The "
                "freeze is inside something nobody here wrote, and there is no "
                "line of yours to open — which is a finding, and not the same "
                "as an empty report.", ""]

    others = [t for t in busy if t is not main and not t.lock]
    if others:
        out += ["## Threads that were doing something", ""]
        # Threads running the app's own code first. The idle vocabulary will
        # never cover every library, and a thread with no frame of this project
        # anywhere in it is the one a reader can do least with — so it sinks
        # rather than being struck out on a guess.
        ranked = sorted(others, key=lambda t: (not t.own(report.prefixes),
                                               t.state.endswith("waiting")))
        for thread in ranked[:12]:
            own = thread.own(report.prefixes)
            where = own[0] if own else thread.top
            out.append(f"- **{thread.name}** ({thread.state}) — `{where}`")
        if len(ranked) > 12:
            out.append(f"- … and {len(ranked) - 12} more")
        out.append("")

    placed, missing = code or ([], [])
    if placed:
        out += ["## Where these frames are in this checkout", "",
                "_the compiler wrote the file and the line into the frame; "
                "nothing here was searched for_", ""]
        for found in placed:
            mark = "" if found.exact else "  ← one of several of that name"
            where = found.file + (f":{found.line}" if found.line else "")
            out.append(f"- `{_short(found.symbol)}` — {where}{mark}")
        out.append("")

    if report.load:
        out += ["## What else the device was doing", "",
                "_the record's own CPU table, over the seconds around the "
                "freeze — a machine that was busy is a different story from "
                "an app that blocked itself_", ""]
        for share, name in report.load[:6]:
            mine = "  ← this app" if report.package and name.startswith(
                report.package) else ""
            out.append(f"- {share}% `{name}`{mine}")
        out.append("")

    gaps = _gaps(report, missing)
    if gaps:
        out += ["## What this report does not say", ""] + gaps + [""]

    return "\n".join(out).rstrip() + "\n"


def _gaps(report: Report, missing: list[str] | None = None) -> list[str]:
    """What could not be read or was never there.

    A tool that answers only what it can answer has to say which questions it
    skipped, or silence reads as a clean bill.
    """
    gaps = []
    if not report.reason:
        gaps.append("- Why the system fired the ANR. A Crashlytics export "
                    "carries no reason, component or intent — those come from "
                    "`dumpsys dropbox` and Play Console.")
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
    if missing:
        gaps.append(f"- where {len(missing)} of this project's frames are: they "
                    f"name a source file this checkout does not have. Either "
                    f"the module is elsewhere or the report is from another "
                    f"version — first is `{missing[0]}`.")
    if report.skipped:
        gaps.append(f"- {report.skipped} line(s) after the threads — the "
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
        "chains": [
            {
                "monitor": chain.monitor,
                "named": chain.named,
                "blocks_main": chain.blocks_main,
                "inferred": chain.inferred,
                "deadlock": chain.cycle,
                "waiters": [t.name for t in chain.waiters],
                "holders": [t.name for t in chain.holders],
                "root": None if chain.root is None else {
                    "name": chain.root.name,
                    "tid": chain.root.tid,
                    "state": chain.root.state,
                    "stack": _stack(chain.root, report.prefixes),
                },
            }
            for chain in chains(report)
        ],
        "main": None if main_thread is None else {
            "state": main_thread.state,
            "idle": idle_reason(main_thread),
            "denied": None if not main_thread.lock else main_thread.lock.cls,
            "stack": _stack(main_thread, report.prefixes),
        },
        "working": [
            {"name": t.name, "state": t.state,
             "where": (t.own(report.prefixes) or [t.top])[0]}
            for t in busy if t is not main_thread and not t.lock
        ],
        "code": None if code is None else {
            "placed": [
                {"symbol": f.symbol, "file": f.file, "line": f.line,
                 "exact": f.exact}
                for f in code[0]
            ],
            "unplaced": code[1],
        },
        "load": [{"share": s, "process": n} for s, n in report.load],
        "own_frames": any(t.own(report.prefixes) for t in of_interest(report)),
        "unread": {
            "outside": len(report.unread),
            "inside": sum(len(t.unread) for t in report.threads),
            "after_the_threads": report.skipped,
        },
    }


def to_json(report: Report,
            code: tuple[list[Located], list[str]] | None = None) -> str:
    return json.dumps(summary(report, code), ensure_ascii=False, indent=2)
