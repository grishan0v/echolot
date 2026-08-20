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

    python -m echolot.anr report.txt
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --- the format -------------------------------------------------------------
#
# Frames are what the sources agree on. Everything around them — how a thread
# is announced, where the lock note lives, what a native frame is prefixed
# with — is each source's own, so it is declared per source rather than
# branched on inside the reader.
#
# `# Key: value` is Crashlytics, bare `Key: value` is the dropbox record. One
# pattern reads both headers; only the thread body needed splitting.
_HEADER = re.compile(r"^#?\s*(?P<key>[A-Za-z][A-Za-z -]*): (?P<value>.*)$")

# The dropbox record wraps the dump: an ANR trace holds one block per process,
# starting with the one that hung, and every block names itself.
_PROCESS = re.compile(r"^-{2,}\s*pid (?P<pid>\d+) at .*-{2,}\s*$")
_CMDLINE = re.compile(r"^Cmd line: (?P<cmd>\S+)")
# `dumpsys dropbox --print <tag>` concatenates every entry it holds.
_ENTRY = re.compile(r"^={20,}\s*$")
# The record's own furniture: block rules, the thread count above a block, and
# the runtime's note about how long suspending everything took. Skipped by
# name so that a line which is genuinely new still reaches the unread count.
_SCAFFOLD = re.compile(
    r"^(?:-{2,}.*-{2,}\s*|DALVIK THREADS \(\d+\):|suspend all histogram:.*)$"
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
# Verified in halves, and the halves are worth telling apart. The record's
# header was read off a live device — `Process`, `Subject`, `Build`, and the
# `====` between entries are as this device writes them. The thread body is
# written from ART's own format and has NOT been checked against a real ANR
# record: the files under `/data/anr/` are mode 600 owned by `system`, and a
# device without root hands them over only inside a bugreport.
#
# So this half fails loudly on purpose. Lines that match nothing are counted
# and printed, a dump that yields no threads exits with the reason, and neither
# quietly produces a report built on a guess.
#
# Two signature forms, both ART's: a managed thread carries `prio` and `tid`,
# and a thread the runtime never attached carries only `sysTid`.
DUMPSYS = Source(
    name="dumpsys",
    signature=re.compile(
        r'^"(?P<name>[^"]*)"(?:\s+daemon)?\s+'
        r"(?:prio=\d+\s+tid=(?P<tid>-?\d+)\s+(?P<state>\S+)"
        r"|sysTid=(?P<systid>\d+))\s*$"
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
)

SOURCES = (CRASHLYTICS, DUMPSYS)

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

    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line.strip():
            continue

        signature = source.signature.match(line)
        if signature:
            fields = {k: v or "" for k, v in signature.groupdict().items()}
            note = fields.pop("note", "")
            current = Thread(state=_state(fields.pop("state", "")),
                             process=process, **fields)
            threads.append(current)
            if source.lock_on_signature:
                held = source.lock.search(note)
                if held:
                    current.lock = Lock(**held.groupdict())
                elif note:
                    current.unread.append(note)
            continue

        if current is not None:
            if _read_body(line, current, source):
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
            current = None
            continue
        if _SCAFFOLD.match(line):
            continue

        # The header runs before the first thread. After one has started, a
        # `Key: value` line is something inside the block and belongs to it.
        if current is None:
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
                    unread=unread, entries=entries)
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
    owner: Thread | None
    cycle: bool = False          # the owner waits, directly or not, on a waiter

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
    """
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
            owner=owner,
            cycle=_waits_back(owner, {t.tid for t in waiters}, by_tid),
        ))
    # Main first, then by how many threads piled up behind the lock.
    return sorted(found, key=lambda c: (not c.blocks_main, -len(c.waiters)))


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


def render(report: Report) -> str:
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
        out += ["## What was holding the lock", "",
                "_a monitor held by a thread that is itself waiting is the "
                "mechanism, not a coincidence — this section needs no trace_", ""]
        for chain in found:
            name = chain.named or chain.monitor
            also = f" (`{chain.monitor}` in the dump)" if chain.named else ""
            who = ", ".join(f"`{t.name}`" for t in chain.waiters[:4])
            if len(chain.waiters) > 4:
                who += f" and {len(chain.waiters) - 4} more"
            out.append(f"**{len(chain.waiters)} denied `{name}`**{also} — {who}")
            if chain.cycle:
                out.append("")
                out.append("> The holder is itself waiting on one of them: "
                           "this is a deadlock.")
            if chain.owner is None:
                out += ["", "Held by a thread that is not in this dump.", ""]
                continue
            out += ["", f"Held by **{chain.owner.name}** (tid {chain.owner.tid}, "
                        f"{chain.owner.state}), which was standing on:", "", "```"]
            out += _stack(chain.owner, report.prefixes)
            out += ["```", ""]

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

    gaps = _gaps(report)
    if gaps:
        out += ["## What this report does not say", ""] + gaps + [""]

    return "\n".join(out).rstrip() + "\n"


def _gaps(report: Report) -> list[str]:
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
    return gaps


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m echolot.anr <report.txt>", file=sys.stderr)
        return 2
    path = Path(args[0])
    if not path.is_file():
        print(f"error: no such file: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8", errors="replace")
    source = detect(text)
    if source is None:
        print(f"error: nothing in {path} announces a thread the way a source "
              f"this reader knows does. It reads the Crashlytics export and "
              f"the ART dump that `dumpsys dropbox --print data_app_anr` and "
              f"the files under /data/anr/ carry.", file=sys.stderr)
        return 1
    report = parse(text, source)
    if not report.threads:
        print(f"error: {path} reads as {source.name} and yields no threads.",
              file=sys.stderr)
        return 1
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
