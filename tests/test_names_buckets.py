"""`names` sorts a slice into GC, locks or binder by the shapes those slices have (#518).

It matched substrings, and on a real app the sections filled with whatever
held one: Kotlin's collections and Compose's JIT compilations under garbage
collection, an app marker and `…Locked` methods under locks, a class whose
name held `ipc` under binder. "Missed by the masks" then listed them and
asked to widen a detector's mask for each. On a scroll trace all nine of its
rows were JIT compilations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echolot.main import _name_sections  # noqa: E402
from tests.support import check  # noqa: E402

GC, LOCKS, BINDER = "Garbage collection", "Locks and waiting", "Binder / IPC"

# Each name with the section it must land in, or None for none of the three.
NAMES = [
    # What the runtime and the platform write for these.
    ("young concurrent copying GC", GC),
    ("concurrent copying GC", GC),
    ("Background concurrent copying GC", GC),
    ("waitWhileAllocatingLocked", GC),
    ("WaitForGcToComplete", GC),
    ("Lock contention on a monitor lock (owner tid: #)", LOCKS),
    ("monitor contention with owner main (#) at void a.b()(:-#) waiters=# "
     "blocking from void c.d()(:-#)", LOCKS),
    ("Lock contention on ClassLinker classes lock (owner tid: #)", LOCKS),
    ("futex_wait", LOCKS),
    ("binder transaction", BINDER),
    ("binder transaction async", BINDER),
    ("binder reply", BINDER),
    ("binder async rcv", BINDER),
    ("AIDL::java::IPackageManager::getPackageInfo::client", BINDER),
    # What a substring caught, from a real cold start and a real scroll.
    ("JIT compiling java.lang.Object kotlinx.collections.immutable.a.b() "
     "(kind=Baseline)", None),
    ("JIT compiling void androidx.compose.runtime.SnapshotDoubleIndexHeap.remove(int) "
     "(kind=Baseline)", None),
    ("JIT compiling void androidx.compose.runtime.snapshots.SnapshotKt"
     ".releasePinnedSnapshotLocked(int) (kind=Baseline)", None),
    ("Lkotlin/collections/CollectionsKt;", None),
    ("Lm#/gc;", None),
    ("AccessibilityNodeInfo$CollectionItemInfo", None),
    ("startup_health_monitor", None),
    ("HealthMonitorInitializer", None),
    ("Choreographer#scheduleVsyncLocked", None),
    ("ResourcesManager#createApkAssetsSupplierNotLocked", None),
    ("Lretrofit2/SkipCallbackExecutorImpl;", None),
    ("(Paused)FlipCallback", None),
    ("perfetto_javaheapprof init.", None),
    ("transactionCallback - ViewRootImpl@#[MainActivity]", None),
    ("LZ4 decompress block", None),
    # The agent's own marker, whatever it is named after.
    ("AGENTTMP_gc_tune", None),
]


def _family() -> dict:
    return {"n": 1, "ns": 50e6, "threads": {"main"}, "dets": set(), "skips": set()}


# Locks, though no mask is meant to see them: the runtime's own.
NOT_MISSED = {"Lock contention on ClassLinker classes lock (owner tid: #)"}


def _where(name: str) -> tuple[str | None, bool]:
    sections, missed = _name_sections({name: _family()}, 0, None, "AGENTTMP_")
    section = next((title for title, items in sections if title != "Everything else"
                    for f, _ in items if f == name), None)
    return section, any(f == name for _, f, _ in missed)


@pytest.mark.parametrize("name,section", NAMES)
def test_each_name_lands_where_its_shape_says(name: str, section: str | None) -> None:
    got, missed = _where(name)
    check(f"{name!r} lands in {section}", got == section, got)
    check("and only a slice of one of the three can be missed by a mask",
          missed == (section is not None and name not in NOT_MISSED), missed)
