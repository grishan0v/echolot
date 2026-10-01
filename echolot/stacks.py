"""What ran in a blind spot, from the callstack samples that fell there.

`uninstrumented_cpu` finds a thread that burned CPU with no slice around it.
Without samples that is all a round can say, and what the thread ran takes
markers, another recording and another look. A trace recorded with
`runner.sampling` holds the answer already. Each of its samples on that
thread, in the stretches no top-level slice covered, names two things:

- what ran: the first Kotlin or Java method with a name, from the top of the
  stack down;
- who asked: the nearest frame of the project's own code under it, where a
  marker would go.

Both rules come from sampled cold starts of a large Kotlin app on an
Android 13 phone, where the obvious ones answered badly:

- The top frame itself is the runtime more often than not. Of 8,092 stacks in
  blind spots, 47% had ART's own code on top — the interpreter, class
  loading, the collector's read barrier — and 21% the C library's, `write`
  and `syscall` first. `nterp_helper` says a method was interpreted, not
  which one. So the runtime's and native frames above the first method are
  passed over, and only a stack with no method named at all is named by its
  native top.
- The stack is often cut short. The framework's compiled code, the boot
  image, comes back without names, and the unwinder stops in it: 69% of those
  stacks ended in `boot.oat`, and 28% reached the thread's start. A stack
  with nothing of the project's on it is therefore two answers. Whole, it is
  `none`: work nobody of ours asked for, or asked for by handing it to a
  pool, whose task runs without its caller. Cut short, it is `cut`: the part
  of the stack that could have said is not there.

Which frames are the project's is `anr.Ownership`'s question, answered the way
it is for an ANR: the packages the checkout's sources declare when there is a
checkout, the report's package and the platform lists when there is not. The
checkout matters here. Without it, a library the lists have not heard of —
a JSON parser, on that phone — reads as the project's own. And the package
alone would not do: a benchmark build is often installed under a suffix of
its own, `com.example.app.beta`, while its code stays in `com.example.app`.
Only frames of the JVM are asked at all: a native `deflate` is in no package,
and the lists would wave it through.
"""

from __future__ import annotations

import functools
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import anr

# How many functions each list in a row's `stacks` keeps. The report is meant
# to stay small, and a busy thread puts hundreds of distinct functions on top
# of its stacks. The ten largest carry the answer, and their shares say how
# much the rest held between them.
KEEP = 10
# How many of them the evidence column names, per list, and how long a name
# there may be. A Kotlin lambda's synthetic name ran past a hundred characters
# on a phone; `report.json` keeps it whole.
SHOWN = 2
LONGEST = 48

# A frame of Kotlin or Java as the unwinder names it: `pkg.Class.method`,
# sometimes with the return type in front and the arguments behind. Two dots
# at least, which a C function never has and a C++ one spells `::`.
_JVM = re.compile(r"^(?:\S+\s+)?(?P<method>[\w$]+(?:\.[\w$<>-]+){2,})(?:\(.*\))?$")
# A shared object, where no frame of the JVM lives: `libz.so`, `libc++.so.1`.
# A compiler's clone of a C function, `inflate.part.0`, has the dots of a Java
# name and is told apart by its file.
_SHARED = re.compile(r"\.so(?:\.\d+)*$")
# The suffixes a compiler gives the copies it makes of one function.
_CLONE = re.compile(r"(?:\.(?:__uniq|llvm|part|isra|constprop|lto_priv)\.\d+|\.cfi|\.cold)+$")
# Where a whole stack ends: a thread's start in bionic, or the process's own
# for the main thread. A stack that ends anywhere else was cut short.
_ENTRY = frozenset({"__start_thread", "__pthread_start", "__libc_init", "_start_main"})

# Every stack the given callsites end, leaf first. The leaf is the callsite a
# sample points at, and `parent_id` walks down to the root. A frame R8 renamed
# has its original name in `deobfuscated_name` once the trace carries the
# mapping, and that is the name a reader can find.
_WALK = """
WITH RECURSIVE chain(leaf, id, hop) AS (
    SELECT id, id, 0 FROM stack_profile_callsite WHERE id IN ({ids})
    UNION ALL
    SELECT chain.leaf, c.parent_id, chain.hop + 1
    FROM chain JOIN stack_profile_callsite c ON c.id = chain.id
    WHERE c.parent_id IS NOT NULL
)
SELECT chain.leaf AS leaf,
       COALESCE(NULLIF(f.deobfuscated_name, ''), f.name) AS name,
       m.name AS mapping
FROM chain
JOIN stack_profile_callsite c ON c.id = chain.id
JOIN stack_profile_frame f ON f.id = c.frame_id
LEFT JOIN stack_profile_mapping m ON m.id = f.mapping
ORDER BY chain.leaf, chain.hop
"""


@dataclass(frozen=True)
class Frame:
    name: str | None      # as the trace names it; None when the unwinder had none
    mapping: str | None   # the file its code was mapped from

    @property
    def method(self) -> str | None:
        """`pkg.Class.method` for a frame of the JVM, None for anything else.

        A mapping that gave several methods one minified name names the frame
        all of them, `pkg.Store.load | pkg.Store.save`, and then each of them
        has to be a method.
        """
        if not self.name or (self.mapping and _SHARED.search(self.mapping)):
            return None
        found = [_JVM.match(part.strip()) for part in self.name.split(" | ")]
        if not all(found):
            return None
        return " | ".join(m.group("method") for m in found)

    @property
    def label(self) -> str:
        """What `report.json` calls it.

        A method by its whole name, a native function by the name a reader
        knows (see `_native`), and a frame with no name by its file: the
        framework's compiled code comes back unnamed from a device, and
        `[boot-framework.oat]` still says whose it was.
        """
        if self.name:
            return self.method or _native(self.name)
        return f"[{Path(self.mapping).name}]" if self.mapping else "[unknown]"

    @property
    def short(self) -> str:
        """What the evidence column calls it: `Store.save`, `ReadBarrier::Mark`.

        The package and the namespace go, since the row has room for two names
        in each list and `report.json` keeps the whole one. An obfuscated class
        has no capital to start from, and keeps its last two parts.
        """
        method = self.method
        if method:
            return " | ".join(_short_method(m) for m in method.split(" | "))
        if self.name:
            return "::".join(_native(self.name).split("::")[-2:])
        return self.label


def _short_method(method: str) -> str:
    parts = method.split(".")
    for i, part in enumerate(parts[:-1]):
        if part[:1].isupper():
            return ".".join(parts[i:])
    return ".".join(parts[-2:])


def _native(name: str) -> str:
    """A native function by the name a reader knows: `art::ReadBarrier::Mark`.

    Three things come off. A compiler's copy of a function carries a suffix
    of its own — `inflate.part.0`, `Field_set.__uniq.2017…` — and is the same
    function. A C++ name arrives mangled, and its qualified name is read out.
    And the parameters go, whichever way the name came.
    """
    name = _CLONE.sub("", name)
    if name.startswith("_Z"):
        name = _demangle(name)
    return name.split("(", 1)[0] if "(" in name else name


# Inside template arguments: a literal (`Lb0E`, `Li42E`), whose digits are a
# value rather than a length; a substitution or a template parameter (`S_`,
# `S0_`, `St`, `T_`), which refers back and has no length either.
_LITERAL = re.compile(r"L[a-zA-Z]n?[0-9a-fA-F]*E")
_BACKREF = re.compile(r"S[0-9A-Z]*_|S[tabsiod]|T[0-9A-Z]*_")
_DIGITS = re.compile(r"\d+")


def _demangle(symbol: str) -> str:
    """The qualified name in an Itanium-mangled symbol, without its parameters.

    `_ZN3art11ReadBarrier4MarkEPNS_6mirror6ObjectE` is `art::ReadBarrier::Mark`.
    Only as much of the scheme as a name takes: nested names, `St` for `std`,
    local names, constructors and destructors, and template arguments, which
    are passed over — `basic_string<…>::append` is `basic_string::append`.
    Where a symbol goes past that — operators, local entities — the name read
    so far is kept, and a symbol with nothing readable comes back as it was.
    """
    nested = symbol.startswith("_ZN")
    i = 3 if nested else 2
    while nested and i < len(symbol) and symbol[i] in "rVKRO":
        i += 1
    parts: list[str] = []
    while i < len(symbol):
        if nested and symbol[i] == "E":
            break
        if symbol.startswith("St", i):
            parts.append("std")
            i += 2
        elif symbol[i] == "L":
            i += 1
        elif symbol[i] == "I" and parts:
            i = _past_arguments(symbol, i)
        elif symbol[i] == "C" and symbol[i + 1:i + 2] in ("1", "2", "3") and parts:
            parts.append(parts[-1])
            i += 2
        elif symbol[i] == "D" and symbol[i + 1:i + 2] in ("0", "1", "2") and parts:
            parts.append("~" + parts[-1])
            i += 2
        else:
            digits = _DIGITS.match(symbol, i)
            if not digits:
                break
            i = digits.end() + int(digits.group())
            parts.append(symbol[digits.end():i])
            if not nested:
                break
    return "::".join(parts) if parts else symbol


def _past_arguments(symbol: str, i: int) -> int:
    """Where the template arguments that open at `i`, an `I`, end."""
    depth = 0
    while i < len(symbol):
        skip = _LITERAL.match(symbol, i) or _BACKREF.match(symbol, i)
        if skip:
            i = skip.end()
            continue
        digits = _DIGITS.match(symbol, i)
        if digits:
            i = digits.end() + int(digits.group())
            continue
        c = symbol[i]
        if c in "INJX":
            depth += 1
        elif c == "E":
            depth -= 1
            if depth == 0:
                return i + 1
        elif c == "D":
            i += 1  # a builtin type spelled in two letters
        i += 1
    return len(symbol)


def chains(tp, callsites: set[int]) -> dict[int, list[Frame]]:
    """Every stack the callsites end, leaf first, by the callsite.

    A frame the unwinder put in to say it gave up — `ERROR INVALID_ELF` and
    the like — names no function, and reads as a frame without a name.
    """
    if not callsites:
        return {}
    ids = ", ".join(str(int(c)) for c in sorted(callsites))
    out: dict[int, list[Frame]] = {}
    for r in tp.query(_WALK.format(ids=ids)):
        name = r.get("name") or None
        if name and name.startswith("ERROR "):
            out.setdefault(int(r["leaf"]), []).append(Frame(None, None))
            continue
        out.setdefault(int(r["leaf"]), []).append(Frame(name, r.get("mapping") or None))
    return out


@functools.lru_cache(maxsize=4)
def ownership(package: str, root: Path | None) -> anr.Ownership:
    """Whose frames are the project's, for the checkout at `root`.

    Cached, because `analyze` asks once per trace and ten repeats would walk
    the checkout ten times. `root` is None where there is no checkout to ask,
    as in the self-check, and the answer is then the package's guess.
    """
    declared = None
    if root is not None and root.is_dir():
        declared = anr.declared_packages(anr.source_index(root)) or None
    return anr.Ownership(package, declared)


def read(samples: list[int | None], stacks: dict[int, list[Frame]],
         ours: anr.Ownership | None) -> tuple[dict[str, Any], str]:
    """What one row's samples say: the block for its `stacks`, and the words for its evidence.

    `samples` holds the callsite of every sample behind the row, None for one
    that came without a stack. The shares are of the samples with a stack,
    since the others name nothing; `samples` and `with_stack` say how many of
    each there were. `ours` None claims no frame, for a trace with no stacks
    to ask about.
    """
    leaves: Counter[str] = Counter()
    named: Counter[str] = Counter()
    whole_none = cut = 0
    shorts: dict[str, str] = {}
    # A busy thread returns to the same few hundred stacks thousands of times,
    # so each stack is read once.
    read_once: dict[int, tuple[Frame, Frame | None, bool]] = {}
    stacked = 0
    for callsite in samples:
        if callsite is None or not stacks.get(callsite):
            continue
        if callsite not in read_once:
            chain = stacks[callsite]
            read_once[callsite] = (_top(chain), _nearest(chain, ours), _whole(chain))
        top, own, whole = read_once[callsite]
        stacked += 1
        leaves[top.label] += 1
        shorts[top.label] = _cap(top.short)
        if own is not None:
            named[own.method] += 1
            shorts[own.method] = _cap(own.short)
        elif whole:
            whole_none += 1
        else:
            cut += 1

    leaf = [{"frame": frame, "samples": n, "pct": _pct(n, stacked)}
            for frame, n in sorted(leaves.items(), key=lambda kv: (-kv[1], kv[0]))]
    mine = _ours(named, whole_none, cut, stacked)
    block: dict[str, Any] = {
        "samples": len(samples),
        "with_stack": stacked,
        "leaf": leaf[:KEEP],
        "ours": mine[:KEEP],
    }
    more = {name: len(found) - KEEP
            for name, found in (("leaf", leaf), ("ours", mine)) if len(found) > KEEP}
    if more:
        block["more"] = more

    if not samples:
        return block, "no samples"
    if not stacked:
        return block, f"{_count(len(samples), 'sample')}, none with a stack"
    what = ", ".join(f"{shorts[e['frame']]} {_share(e['samples'], stacked)}"
                     for e in leaf[:SHOWN])
    who = ", ".join(f"{_word(e, shorts)} {_share(e['samples'], stacked)}"
                    for e in mine[:SHOWN])
    return block, f"{_count(stacked, 'stack')}: {what} · ours: {who}"


def _top(chain: list[Frame]) -> Frame:
    """What a stack says ran: its first named method, else its first name, else its top."""
    return (next((f for f in chain if f.method), None)
            or next((f for f in chain if f.name), None)
            or chain[0])


def _nearest(chain: list[Frame], ours: anr.Ownership | None) -> Frame | None:
    """The frame of the project's own code closest to the top, if any.

    A frame a mapping named as several methods is ours when one of them is:
    R8 gives one name to methods of one class, and the class is what decides.
    """
    if ours is None:
        return None
    return next((f for f in chain if f.method
                 and any(ours.claims(m) for m in f.method.split(" | "))), None)


def _whole(chain: list[Frame]) -> bool:
    """Whether the stack goes down to where its thread began."""
    root = chain[-1].name
    return root is not None and _native(root) in _ENTRY


def _ours(named: Counter, whole_none: int, cut: int, total: int) -> list[dict[str, Any]]:
    """Frames of ours, largest first, with the stacks that had none among them.

    A stack without a frame of ours is `"stack": "whole"` when it reached its
    thread's start and `"stack": "cut"` when it ended before. On a tie a frame
    comes first, then the whole stacks, then the cut ones, and frames by name.
    """
    ranked = [(n, 0, frame, None) for frame, n in named.items()]
    ranked += [(n, kind, "", stack) for n, kind, stack
               in ((whole_none, 1, "whole"), (cut, 2, "cut")) if n]
    out = []
    for n, _, frame, stack in sorted(ranked, key=lambda r: (-r[0], r[1], r[2])):
        entry: dict[str, Any] = {"frame": frame or None, "samples": n, "pct": _pct(n, total)}
        if stack:
            entry["stack"] = stack
        out.append(entry)
    return out


def _word(entry: dict[str, Any], shorts: dict[str, str]) -> str:
    if entry["frame"]:
        return shorts[entry["frame"]]
    return "none" if entry.get("stack") == "whole" else "cut"


def _cap(name: str) -> str:
    return name if len(name) <= LONGEST else name[:LONGEST - 1] + "…"


def _pct(n: int, total: int) -> float:
    return round(100 * n / total, 1)


def _share(n: int, total: int) -> str:
    """A whole percent, rounded half up; a share too small to show one says so."""
    share = 100 * n / total
    return "<1%" if share < 0.5 else f"{int(share + 0.5)}%"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"
