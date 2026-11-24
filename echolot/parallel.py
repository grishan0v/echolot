"""Reading the traces of a set side by side (#488).

`analyze` read a set one trace after another. Each trace is read by a
trace_processor of its own, and all the work of reading it is done in that
process: echolot sends it SQL and waits. So on a laptop with eleven cores one
of them worked through fifteen cold starts and ten waited, while
`collect -n 15` makes exactly such sets.

Threads, not processes: what a worker does is wait for its trace_processor,
and the GIL is not held while it waits. What has to hold is what the run
looked like one trace at a time:

- **The order.** The reports come back in the order the traces were given,
  however the workers finish, and `report.aggregate` merges them in that
  order. So does everything a trace's reading printed: each worker's lines
  are kept, and printed whole, trace by trace, once every trace before it
  is done. A warning about the second trace comes after the first trace's
  lines, never in the middle of them.
- **The first failure.** A trace that cannot be read ends the run with its
  own error, as before: the first one in the order given, after what the
  traces before it printed. The traces after it are not waited for — their
  sessions are closed, and what they printed is dropped, as it was never
  printed before. A trace_processor that fails is such a failure too, said
  with the trace it was reading (tpshell.Failed); a socket error while it
  runs is tried once more first, since a dozen shells at once is when a
  machine runs short of sockets (#871).
- **The end.** Ctrl-C, SIGTERM and SIGHUP reach Python's main thread only,
  and a worker's trace_processor is out of their reach (orphans.py). The
  main thread holds the signals for the whole run and closes every session
  of the group on its way out, whatever ended it.

How many at once: as many as there are cores, traces, and memory for. A
trace_processor holds its whole trace, up to twelve times its size on disk
(up to 250 MB for cold starts of 16–24 MB), and half the machine's memory is
what the set may take. `-j N` says otherwise, and `-j 1` is the old way, in this
thread, with nothing kept back.
"""

from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from . import orphans

# What one trace_processor takes for a trace of this size, as a multiple of
# the file, and a floor for the process itself: on the safe side of cold
# starts of 16–24 MB, which took up to 250 MB each, 2.1 GB for eleven at once.
MEMORY_PER_BYTE = 12
MEMORY_FLOOR = 128 << 20
# The part of the machine's memory the set may take at once.
MEMORY_SHARE = 0.5


def cores() -> int:
    """The cores this process may run on."""
    count = getattr(os, "process_cpu_count", None) or os.cpu_count
    return max(1, count() or 1)


def memory() -> int | None:
    """The machine's memory in bytes; None where it cannot be read."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return None


def jobs_for(traces: Sequence[str | Path], asked: int | None = None, *,
             cpus: int | None = None, ram: int | None = None) -> int:
    """How many of these traces to read at once.

    `asked` is `-j`, and wins over the rest except the number of traces.
    Otherwise the smallest of the traces, the cores, and how many of the
    largest trace fit in the share of memory. Never less than one.
    """
    if not traces:
        return 1
    if asked:
        return max(1, min(asked, len(traces)))
    cpus = cpus if cpus is not None else cores()
    ram = ram if ram is not None else memory()
    fit = cpus
    if ram:
        sizes = []
        for t in traces:
            try:
                sizes.append(Path(t).stat().st_size)
            except OSError:
                sizes.append(0)
        each = MEMORY_PER_BYTE * max(sizes) + MEMORY_FLOOR
        fit = int(ram * MEMORY_SHARE // each)
    return max(1, min(len(traces), cpus, fit))


class _Kept:
    """sys.stdout or sys.stderr while workers run: each worker's writes kept.

    A thread that has a list to keep them in writes there, with the stream's
    name beside each piece, so that one list holds a worker's stdout and
    stderr in the order it wrote them. Any other thread — the main one —
    writes through. Everything else is the real stream's: whether it is a
    terminal, its encoding, its file descriptor.
    """

    def __init__(self, stream, name: str, local: threading.local) -> None:
        self._stream = stream
        self._name = name
        self._local = local

    def write(self, text: str) -> int:
        said = getattr(self._local, "said", None)
        if said is None:
            return self._stream.write(text)
        said.append((self._name, text))
        return len(text)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        if getattr(self._local, "said", None) is None:
            self._stream.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


@dataclass
class _Outcome:
    value: Any = None
    error: BaseException | None = None
    said: list[tuple[str, str]] = field(default_factory=list)


def in_order(items: Sequence[Any], work: Callable[[Any], Any], jobs: int) -> list:
    """`work(item)` for each item, `jobs` at a time, as if one after another.

    The results in the order of `items`, the lines each printed after the
    lines of every item before it, and the first failure in that order
    raised — after what the items before it printed, and its own. With one
    job, or one item, it is a plain loop in this thread.
    """
    if jobs <= 1 or len(items) <= 1:
        return [work(item) for item in items]

    local = threading.local()
    real = {"stdout": sys.stdout, "stderr": sys.stderr}
    group = orphans.Group()

    def one(item) -> _Outcome:
        outcome = _Outcome()
        local.said = outcome.said
        try:
            with group.joined():
                outcome.value = work(item)
        except BaseException as e:  # handed to the main thread, in order
            outcome.error = e
        finally:
            local.said = None
        return outcome

    def replay(said: list[tuple[str, str]]) -> None:
        for name, text in said:
            real[name].write(text)
        for stream in real.values():
            stream.flush()

    held = orphans.hold_signals()
    sys.stdout = _Kept(real["stdout"], "stdout", local)
    sys.stderr = _Kept(real["stderr"], "stderr", local)
    pool = ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="echolot-trace")
    results = []
    try:
        futures = [pool.submit(one, item) for item in items]
        for future in futures:
            outcome = future.result()
            replay(outcome.said)
            if outcome.error is not None:
                raise outcome.error
            results.append(outcome.value)
    except BaseException:
        # A failure, Ctrl-C or a signal: what is still running is closed
        # from here, what has not started never starts, and the workers are
        # waited for — each ends at its next query.
        group.stop()
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)
    finally:
        sys.stdout, sys.stderr = real["stdout"], real["stderr"]
        if held:
            orphans.let_go()
    return results
