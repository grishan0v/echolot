"""One name for one thing: the numbers the runtime chose, folded once (#831).

Slice and thread names carry numbers the runtime picks anew in every run: a
thread id inside a lock note, a frame's vsync id, the number of a pool's
worker. Two names that differ only there are one phenomenon, and one row.

`runtime_name` is the one rule that says which numbers those are. `analyze`
applies it once per trace, to every slice and thread name in the window, and
writes the result down as `_name_family`; a detector that groups by a name
groups by `COALESCE(f.family, name)`. So a row's name is folded before its
numbers are added up, the same way in every detector, in a report of one
recording and in a set (#504, #714, #728, #511 each folded it somewhere else,
with a rule of their own).

The two looser rules below are for one question only: is a name of one build
the same as a name of another? `compare` asks it as its second matching pass,
`names` to keep an inventory short. They fold every number, which no row's
name may do: `Store.java:41` in a lock note is a place in the code.

The last part is the other way round: a name the user wrote, held against
the names of a trace (#1012). An anchor, a `domains` entry or a detector's
mask matches a slice of that very name, and every slice it matches as a
GLOB besides. A renderer that names its sections by subsystem writes
`[render] DrawFrame`, `probe` showed it so, and as a GLOB alone the `[render]`
was a class of one letter: the name written as shown matched nothing.
"""

from __future__ import annotations

import fnmatch
import re

from .tp import sql_value

# Any number: what the matches across builds fold.
NUMBER = re.compile(r"0x[0-9a-fA-F]+|\d+")

# The numbers inside a name that the runtime picks anew in every run, and only
# those (#504). Every digit is right for a match across builds and wrong for a
# row's name: `(Store.java:41)` in a lock note is where the code is, and
# folding it would cost `place` its line.
_RUNTIME = [
    # An address: `Lock contention on 0x7f3a2b`.
    (re.compile(r"0x[0-9a-fA-F]+"), "0x#"),
    # A tid: `(owner tid: 13533)`, `tid 4202`, and ART's `for tid=23762` (#847).
    (re.compile(r"\b(tid[:=]?\s*)\d+"), r"\g<1>#"),
    # The owner's tid in ART's long lock note: `with owner Thread-3 (4455) at`.
    (re.compile(r"(with owner .+? \()\d+(\))"), r"\g<1>#\g<2>"),
    # How many waited, in the same note: `waiters=0`, `waiters=1`.
    (re.compile(r"\b(waiters=)\d+"), r"\g<1>#"),
    # A frame's vsync id, and what a resync says beside it:
    # `Choreographer#doFrame 55112`, `… - resynced to 55120 in 21.1ms`.
    (re.compile(r"(Choreographer#doFrame\b.*)$"),
     lambda m: NUMBER.sub("#", m.group(1))),
    # A binder thread's pid: `binder:1234_5`.
    (re.compile(r"\b((?:Hw)?[Bb]inder:)\d+"), r"\g<1>#"),
    # The number of a pool's or an unnamed thread: `Thread-31`,
    # `pool-3-thread-2`, `DefaultDispatcher-worker-3`, `AsyncTask #2`,
    # `binder:#_5`. After `-`, `_` or `#` only, so `Store.java:41` and
    # `Lambda$1` keep theirs, and so does the unknown line `(:-1)`.
    (re.compile(r"(?<=[-_])(?<!:-)\d+\b"), "#"),
    (re.compile(r"#\d+\b"), "#"),
]


def runtime_name(name: str, keep: str | None = None,
                 names: frozenset[str] | set[str] = frozenset()) -> str:
    """A name with the numbers the runtime chose folded to `#`.

    One wait that happens in every run is one row: `Lock contention on a
    monitor lock (owner tid: 15427)` in one run and `… 10359` in the next are
    the same wait, and `Thread-31` … `Thread-39` across fifteen runs are one
    unnamed thread, started once in each. Merged by the raw name, a 15/15
    wait read as fifteen 1/15 accidents (#504).

    `keep` is the instrumentation prefix and `names` the project's own marker
    names, which are left as they are: their digits were written by someone
    to tell two things apart — see `across_builds`.
    """
    if name in names or (keep and name.startswith(keep)):
        return name
    for pattern, to in _RUNTIME:
        name = pattern.sub(to, name)
    return name


def across_builds(name: str, keep: str | None = None) -> str:
    """A name with every number folded: the looser match across two builds.

    Never a row's name, which `runtime_name` gives. Used by `names` to keep
    an inventory of a real trace from running to thousands of rows, and by
    `compare` as its second matching pass: without it a thread pool that
    handed the work to another worker reads as one row vanishing and an
    unrelated one appearing.

    `keep` is a prefix whose numbers are nobody's accident. Everything this
    collapses — a tid inside a lock note, a frame number, worker-2 against
    worker-5 — is a number the runtime chose, and two names differing only
    there are one phenomenon. A marker the agent placed is the opposite case:
    it wrote the digits itself, to tell two things apart.

    That cost a real finding. An agent instrumenting a migration ladder wrote
    `AGENTTMP_fill_v4` and `AGENTTMP_fill_v6` around the two rungs, and got
    back one row — `AGENTTMP_fill_v# · N=2 · 1447 ms`. One stage that happens
    twice looks ordinary; two rungs where the second repeats the first is the
    bug it was hunting. The tool had both rows and merged them.
    """
    if keep and name.startswith(keep):
        return name
    # One pass: the digits pass after the hex one rewrote its `0x#` as `#x#`.
    return NUMBER.sub(lambda m: "0x#" if m.group(0).startswith("0x") else "#", name)


# Where an installed APK lives: `/data/app/~~<random>==/<package>-<random>==/`
# since Android 11, `/data/app/<package>-<random>==/` before it. Picked anew at
# every install, so the two builds of a comparison name every JIT compilation
# of the app's own code differently: `JIT compiling void Foo.bar() (kind=
# Baseline) from /data/app/~~…`. A package name holds no `-`, so the first one
# ends it.
_INSTALL = re.compile(r"/data/app/(~~[^/]+/)?([^/]+?)-[^/]+/")
# `across_builds`' numbers, and an address `runtime_name` already wrote as
# `0x#`: folded by the pattern of the numbers, its `0` made it `#x#`.
_DIGITS = re.compile(r"0x(?:[0-9a-fA-F]+|#)|\d+")


def slice_across_builds(name: str, keep: str | None = None,
                        names: frozenset[str] | set[str] = frozenset()) -> str:
    """A slice's name as two builds' traces can share it (#610).

    Every number folded, as `across_builds` folds them, and the APK's install
    directory as well. `runtime_name` folds only the numbers the runtime
    picks anew in every run, which is right for one row across repeats of
    one build and leaves too much for a name across two: a fence number
    (`waiting for HWC release 313`) is not among them, and on a real cold
    start it made five names of one set that the other set had under
    other numbers. A line number in a lock note is one place in the code,
    and a build that moved the code by two lines would show it gone and new.

    `keep` and `names` are the project's own markers, kept as written: see
    `across_builds`. Applied to a name `runtime_name` folded, it gives what it
    gives for the raw one.
    """
    if name in names or (keep and name.startswith(keep)):
        return name
    name = _INSTALL.sub(lambda m: f"/data/app/{'~~#/' if m.group(1) else ''}{m.group(2)}-#/",
                        name)
    return _DIGITS.sub(lambda m: "0x#" if m.group(0).startswith("0x") else "#", name)


# --- a name the user wrote (#1012) ---------------------------------------------
#
# Read two ways at once, and never one or the other by guessing: as the name
# it is, and as a GLOB. A GLOB alone took `[render] DrawFrame` for one of the
# letters r, e, n, d followed by ` DrawFrame`, and the name written as the
# trace has it, as `probe` showed it, matched nothing. Read as a name first
# and a GLOB only when no slice had that name, the same anchor would have cut
# two recordings two ways. Both at once, a GLOB keeps every wildcard it has,
# and a written name is never worse off than before: what it matched it still
# matches. What it costs is that a name with a `[`, `*` or `?` of its own
# matches what its GLOB matches as well — `d DrawFrame` for the name above.
# `escaped` writes the GLOB that matches one name and nothing else.

_GLOB_SPECIAL = re.compile(r"([\[*?])")


def escaped(name: str) -> str:
    """A name as the GLOB that matches it and nothing else: each `[`, `*` and
    `?` of its own in a class of one, `[[]render] DrawFrame`."""
    return _GLOB_SPECIAL.sub(r"[\1]", name)


def matches(name: str, written: str) -> bool:
    """Whether a slice's name is what the user wrote: that very name, or one
    the GLOB matches."""
    return name == written or fnmatch.fnmatchcase(name, written)


def matches_sql(column: str, written: str) -> str:
    """`matches` as an SQL condition over `column`."""
    value = sql_value(written)
    return f"({column} = '{value}' OR {column} GLOB '{value}')"


def _folded(name: str) -> str:
    """Each number of a name written as `*`, an address as `0x*`."""
    return NUMBER.sub(lambda m: "0x*" if m.group(0).startswith("0x") else "*", name)


def as_written(name: str) -> str:
    """A slice's name as an anchor is written to match it in every recording.

    A number that differs in every recording, a frame's vsync id or a tid,
    is written `*`. A name with no number is written as it is: it matches
    itself. One with a number is a GLOB, in which a `[`, `*` or `?` of the
    name's own is escaped: `[[]render] Frame *` for `[render] Frame 12`.
    """
    folded = _folded(name)
    return name if folded == name else _folded(escaped(name))
