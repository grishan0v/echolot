"""The self-check: proof that the pipeline computes correctly on THIS machine.

The list of checks below is the fixture's specification in executable form:
what the detectors must find and, just as importantly, what they must NOT. A
false positive costs the agent more than a miss — it will go off investigating
a problem that does not exist and burn its context window.

Checking that dependencies are installed is deliberately absent — pip fails
loudly without us. The value is elsewhere: showing that the environment gives
CORRECT answers on a trace whose contents are known.

Run through `echolot doctor`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import fixture
from .config import Config, ConfigError

# The config is kept in memory rather than in a file: the self-check must not
# depend on what happens to lie on disk nearby. The detectors section is
# deliberately absent, so every detector is enabled with the defaults from the
# .sql files themselves. That is exactly how the defaults a user gets without
# configuring anything are exercised.
FIXTURE_CONFIG = {
    "project": {"package": "com.example.app", "process": "com.example.app"},
    "scenario": {
        "name": "fixture",
        "start": {"name": "AppStart"},
        "end": {"name": "Screen.firstFrame"},
    },
}

CHECKS: list[tuple[str, object]] = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# --- helpers ---------------------------------------------------------------

def rows(report, detector_id):
    for d in report["detectors"]:
        if d["id"] == detector_id:
            if d["error"]:
                raise AssertionError(f"the detector failed: {d['error']}")
            return d["rows"]
    raise AssertionError(f"detector {detector_id} is not in the report")


def locations(report, detector_id):
    return [r["location"] for r in rows(report, detector_id)]


def only_row(report, detector_id):
    got = rows(report, detector_id)
    assert len(got) == 1, f"expected one row, got {len(got)}: {got}"
    return got[0]


def no_slice_named(report, needle):
    """No detector may show this slice."""
    for d in report["detectors"]:
        for r in d["rows"]:
            blob = f"{r.get('location')} {r.get('detail')}"
            assert needle not in blob, \
                f"{needle} surfaced in detector {d['id']}: {r}"


# --- the window and the process --------------------------------------------

@check("scenario window built from the anchors: 1005 ms")
def _(report):
    assert report["window"]["duration_ms"] == 1005.0, report["window"]


@check("the right process is picked, the foreign one is dropped")
def _(report):
    assert report["window"]["process"] == "com.example.app"
    no_slice_named(report, "other_app_huge_OUTSIDE")


@check("both anchors actually matched in the trace")
def _(report):
    w = report["window"]
    assert w["start_anchor"]["matches"] == 1, w["start_anchor"]
    assert w["end_anchor"]["matches"] == 1, w["end_anchor"]


@check("all six detectors fired")
def _(report):
    s = report["summary"]
    assert s["detectors_run"] == 6, s
    assert s["detectors_fired"] == 6, s["fired_ids"]


@check("slices outside the window stayed out of the report")
def _(report):
    no_slice_named(report, "Bootstrap_OUTSIDE")   # 50 ms before the window
    no_slice_named(report, "After_OUTSIDE")       # 200 ms after the window


# --- detectors -------------------------------------------------------------

@check("main_thread_block: found the 120 ms, skipped the 5 ms")
def _(report):
    loc = locations(report, "main_thread_block")
    assert "collection_mapping" in loc, loc
    assert "quick_thing" not in loc, "a 5 ms slice must not clear a 16 ms bar"
    row = next(r for r in rows(report, "main_thread_block")
               if r["location"] == "collection_mapping")
    assert row["total_ms"] == 120.0, row
    assert row["self_ms"] == 120.0, f"no children — self equals total: {row}"


@check("main_thread_block: a wrapper's self time is below its total")
def _(report):
    # AppStart runs for 1006 ms but holds every other slice of the window
    # inside it. It used to arrive in the report with its full 1006 ms
    # alongside its children, and the same time was counted twice. On a live
    # trace that duplicated a 355 ms frame three ways: doFrame →
    # doFrame-resynced → traversal, three rows about one event.
    row = next(r for r in rows(report, "main_thread_block")
               if r["location"] == "AppStart")
    assert row["total_ms"] == 1006.0, row
    assert row["self_ms"] < row["total_ms"], f"children not subtracted: {row}"
    assert row["self_ms"] == 671.0, f"1006 minus 335 ms of children: {row}"


@check("main_thread_block: self time never exceeds the window")
def _(report):
    # The defining property of self time: the terms do not overlap. If the sum
    # exceeds the window, double counting has crept back in somewhere.
    total_self = sum(r["self_ms"] for r in rows(report, "main_thread_block"))
    window = report["window"]["duration_ms"]
    assert total_self <= window, (
        f"self time sums to {total_self} ms against a {window} ms window"
    )


@check("binder_txn: fired on the long transaction, async does not count")
def _(report):
    row = only_row(report, "binder_txn")
    # The group fires because of the 25 ms one, but both synchronous
    # transactions go into the sum: 25 + 3. Showing "25 ms total" would
    # misstate the cost.
    assert row["max_ms"] == 25.0, row
    assert row["count"] == 2, f"both sync transactions belong to the group: {row}"
    assert row["total_ms"] == 28.0, row
    assert row["detail"] == "main thread", row


@check("the async transaction is still visible as a long slice on main")
def _(report):
    # Dropping it from binder_txn does not mean hiding it: the user still sees
    # 40 ms on the main thread, and main_thread_block must say so.
    assert "binder transaction async" in locations(report, "main_thread_block")


@check("monitor_contention: a thread's blocks collapse into one row")
def _(report):
    # main has two blocks with DIFFERENT owners (tid 4201 and 4202). The tid
    # sits inside the slice name, so grouping by name would give two rows of 30
    # and 12 ms instead of one finding of 42. On a live trace 190 blocks
    # scattered exactly that way and the detector stayed silent.
    got = [r for r in rows(report, "monitor_contention")
           if r["location"] == "m.example.app"]
    assert len(got) == 1, f"the finding shattered across owners: {got}"
    assert got[0]["count"] == 2, got[0]
    assert got[0]["total_ms"] == 42.0, got[0]
    assert got[0]["max_ms"] == 30.0, got[0]
    # The evidence carries the name of the LONGEST block, with its owner's tid.
    assert "owner tid: 4201" in got[0]["detail"], got[0]


@check("monitor_contention: runtime-internal locks are filtered out")
def _(report):
    # 'Lock contention on GC lock' is an ART-internal lock with no application
    # code behind it. The old '*ock contention*' mask dragged it in; after the
    # narrowing against a live trace (see the detector header) it must not.
    details = [r["detail"] for r in rows(report, "monitor_contention")]
    assert not any("GC lock" in d for d in details), (
        f"over-matching on runtime locks is back: {details}"
    )


@check("gc_pressure: 20 collection cycles totalling 80 ms")
def _(report):
    got = [r for r in rows(report, "gc_pressure")
           if r["location"] == "Background young concurrent copying GC"]
    assert len(got) == 1, rows(report, "gc_pressure")
    assert got[0]["count"] == 20, got[0]
    assert got[0]["total_ms"] == 80.0, got[0]


@check("gc_pressure: phases inside a cycle are not a separate finding")
def _(report):
    # CopyingPhase lives INSIDE the GC slice. The old thread mask
    # (HeapTaskDaemon*) pulled the phases in alongside the cycle — the same
    # time twice. On a live trace that gave CopyingPhase 295 ms against 282 ms
    # for the whole cycle.
    assert "CopyingPhase" not in locations(report, "gc_pressure"), (
        f"double counting of phases is back: {locations(report, 'gc_pressure')}"
    )


@check("gc_pressure: sees the other side too — waiting on an allocation")
def _(report):
    # waitWhileAllocatingLocked appears on application threads when an
    # allocation stalls waiting for the collector. A real name from Android 14.
    assert "waitWhileAllocatingLocked" in locations(report, "gc_pressure")


@check("runnable_starvation: 50 ms in state R")
def _(report):
    row = only_row(report, "runnable_starvation")
    assert row["location"] == "OkHttp Dispatcher", row
    assert row["total_ms"] == 50.0, row


@check("uninstrumented_cpu: found the thread without a single slice")
def _(report):
    got = [r for r in rows(report, "uninstrumented_cpu")
           if r["location"] == "DefaultDispatcher-worker-1"]
    assert len(got) == 1, rows(report, "uninstrumented_cpu")
    assert got[0]["total_ms"] == 300.0, f"Running inside the window: {got[0]}"
    assert got[0]["count"] == 0, got[0]
    assert got[0]["covered_ms"] == 0.0, got[0]
    # WellInstrumented: 180 ms of slices over 200 ms Running → not blind
    # HeapTaskDaemon:    80 ms of slices over 100 ms Running → not blind


@check("uninstrumented_cpu: coverage is measured on CPU time, not wall clock")
def _(report):
    # BlockingIO-1 sleeps inside its slice: the slice runs 300 ms while the
    # thread is on CPU for only 60 of them, plus 100 ms of Running with no
    # slices at all. Comparing slice duration against on-CPU time would give
    # 300 of 160 = 188% and hide the blind spot. A live cold start showed
    # exactly that: 217% on the main thread.
    got = [r for r in rows(report, "uninstrumented_cpu")
           if r["location"] == "BlockingIO-1"]
    assert len(got) == 1, (
        "a thread sleeping inside a slice must still count as blind: "
        f"{rows(report, 'uninstrumented_cpu')}"
    )
    assert got[0]["total_ms"] == 160.0, got[0]
    assert got[0]["covered_ms"] == 60.0, (
        f"expected the Running/slice intersection, not the slice length: {got[0]}"
    )


@check("uninstrumented_cpu: coverage counts nested slices only once")
def _(report):
    # main: 995 ms of Running inside the window, with the AppStart slice
    # covering the whole window on top. Adding parent to children would exceed
    # everything the thread ever did. The thread is not blind either way, but
    # the arithmetic has to be honest or on a real trace it will mask a genuine
    # blind spot.
    assert "m.example.app" not in locations(report, "uninstrumented_cpu")


# --- calibration -----------------------------------------------------------

@check("calibrate: the measuring pass opens thresholds and strips LIMIT")
def _(report):
    from .main import DETECTOR_DIR
    from .tp import load_detectors
    d = next(x for x in load_detectors(DETECTOR_DIR)
             if x.id == "main_thread_block")
    sql = d.render_open()
    # A statistic over a truncated top twenty would be a statistic over the tail.
    assert "LIMIT" not in sql.upper(), "LIMIT must be stripped while measuring"
    # The threshold is zeroed so HAVING lets the whole distribution through.
    assert ">= 0 * 1000000" in sql, sql[-300:]


@check("calibrate: topN takes the Nth largest, not a percentile")
def _(report):
    from .tp import Calibration
    values = [10.0, 50.0, 30.0, 20.0, 40.0]
    assert Calibration("x", "top", 1, "c", 1.0).value(values) == 50.0
    assert Calibration("x", "top", 3, "c", 1.0).value(values) == 30.0
    # A rank, not a share: the same value holds on a twice-larger sample.
    assert Calibration("x", "top", 3, "c", 1.0).value(values * 2) == 40.0


@check("calibrate: every directive points at a live param and column")
def _(report):
    from .main import DETECTOR_DIR
    from .tp import load_detectors
    from .report import COLUMNS
    for d in load_detectors(DETECTOR_DIR):
        for c in d.calibrations:
            # parse_meta already validates the param; here it is the column —
            # a typo there would silently yield an empty sample and a "kept the
            # default".
            assert c.column in COLUMNS, (
                f"{d.id}: {c.expr} refers to a column outside the report "
                f"contract"
            )


@check("collect: a previous set of traces is set aside, never overwritten")
def _(report):
    from .runner import set_aside
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "traces"
        assert set_aside(out, "run", log=lambda s: None) is None, "nothing to move yet"
        out.mkdir()
        for i in range(3):
            (out / f"run_iter{i:03d}.perfetto-trace").write_bytes(b"x")
        (out / "other_iter000.perfetto-trace").write_bytes(b"y")
        said = []
        aside = set_aside(out, "run", log=said.append)
        assert aside and aside.parent == out and aside.name.startswith("run-"), aside
        assert sorted(p.name for p in aside.iterdir()) == [
            f"run_iter{i:03d}.perfetto-trace" for i in range(3)], list(aside.iterdir())
        assert not list(out.glob("run_iter*.perfetto-trace")), "the old names are free again"
        assert (out / "other_iter000.perfetto-trace").exists(), "another scenario is not touched"
        assert said and "set aside" in said[0], said


@check("thresholds: --set is typed and refuses what does not exist")
def _(report):
    from .main import DETECTOR_DIR, parse_set
    from .tp import load_detectors
    dets = load_detectors(DETECTOR_DIR)
    got = parse_set(["main_thread_block.min_slice_ms=16",
                     "binder_txn.name_glob=binder*",
                     "gc_pressure.max_total_ms=4.5"], dets)
    assert got == {"main_thread_block": {"min_slice_ms": 16},
                   "binder_txn": {"name_glob": "binder*"},
                   "gc_pressure": {"max_total_ms": 4.5}}, got
    for bad in ("nope.min_slice_ms=1", "main_thread_block.nope=1", "garbage"):
        try:
            parse_set([bad], dets)
        except ConfigError:
            continue
        raise AssertionError(f"--set accepted '{bad}'")


@check("thresholds: the report says where each detector's numbers came from")
def _(report):
    from .main import plan_detectors
    # every detector in the fixture run uses the shipped defaults
    assert all(d["params_source"] == "default" for d in report["detectors"]), \
        [(d["id"], d["params_source"]) for d in report["detectors"]]
    assert not any("defaults" in d for d in report["detectors"])

    # The rules, without a trace: which detectors, which overrides, from
    # where. Four analyze runs here once cost doctor four seconds.
    cfg = Config({**FIXTURE_CONFIG,
                  "detectors": {"main_thread_block": {"min_slice_ms": 40}}})
    plan = plan_detectors(cfg)
    # the config lists one detector: only it runs, from the config
    assert [d.id for d, _, _ in plan] == ["main_thread_block"], plan
    d, over, src = plan[0]
    assert src == "config" and over == {"min_slice_ms": 40} and d.params["min_slice_ms"] == 16
    # --set on a detector the config leaves out brings it in, marked cli
    plan = plan_detectors(cfg, cli_overrides={"gc_pressure": {"max_events": 1}})
    by_id = {d.id: (over, src) for d, over, src in plan}
    assert set(by_id) == {"main_thread_block", "gc_pressure"}, set(by_id)
    assert by_id["gc_pressure"] == ({"max_events": 1}, "cli"), by_id
    # --set on top of the config: cli wins, both are named
    plan = plan_detectors(cfg, cli_overrides={"main_thread_block": {"min_slice_ms": 5}})
    _, over, src = plan[0]
    assert src == "config+cli" and over["min_slice_ms"] == 5, (over, src)
    # --defaults: every detector, shipped numbers, the config's list ignored
    plan = plan_detectors(cfg, use_defaults=True)
    assert len(plan) == 6 and all(src == "default" and not over for _, over, src in plan), plan


@check("analyze: a relative -o is taken from the config's directory")
def _(report):
    from .main import _out_dir
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "echolot.yml").write_text("project:\n  process: app\n", encoding="utf-8")
        cfg = Config.load(root / "echolot.yml")
        assert _out_dir(".echolot/out", cfg) == root / ".echolot" / "out"
        assert _out_dir("/abs/elsewhere", cfg) == Path("/abs/elsewhere")
        # an in-memory config has no directory: cwd it is, as before
        assert _out_dir(".echolot/out", Config({})) == Path(".echolot/out")


# --- local.yml -------------------------------------------------------------

def _write_pair(root: Path, project: str, local: str | None) -> Path:
    path = root / "echolot.yml"
    path.write_text(project, encoding="utf-8")
    if local is not None:
        (root / "local.yml").write_text(local, encoding="utf-8")
    return path


@check("local.yml: overrides precisely, without wiping its section neighbours")
def _(report):
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_pair(
            Path(tmp),
            "project:\n  process: com.example.app\n"
            "runner:\n  iterations: 5\n  duration_ms: 12000\n",
            "runner:\n  device: ABC123\n  duration_ms: 9000\n")
        cfg = Config.load(path)

    assert cfg.local_path is not None, "the adjacent local.yml was not picked up"
    assert cfg.runner["duration_ms"] == 9000, "the local value did not override"
    assert cfg.runner["device"] == "ABC123", "the local value was not added"
    # The merge is recursive: a whole section is never swapped out.
    assert cfg.runner["iterations"] == 5, "a section neighbour was wiped"
    assert cfg.process == "com.example.app", "another section was wiped"


@check("local.yml: its absence alongside is normal, not an error")
def _(report):
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config.load(_write_pair(
            Path(tmp), "project:\n  process: com.example.app\n", None))
    assert cfg.local_path is None
    assert cfg.process == "com.example.app"


@check("local.yml: named explicitly and missing is an error, not silence")
def _(report):
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_pair(Path(tmp), "project:\n  process: x\n", None)
        try:
            Config.load(path, Path(tmp) / "no-such-file.yml")
        except ConfigError:
            return
    raise AssertionError("stayed quiet about a missing file named explicitly")


# --- the domains map -------------------------------------------------------

def _sample_repo(root: Path) -> None:
    """A mini repo: two modules, three forms of instrumentation, one trap."""
    (root / "build.gradle.kts").write_text("", encoding="utf-8")
    for module in ("app", "feature/collection"):
        (root / module).mkdir(parents=True, exist_ok=True)
        (root / module / "build.gradle.kts").write_text("", encoding="utf-8")

    src = root / "feature/collection/src/main/kotlin"
    src.mkdir(parents=True)
    (src / "Mapper.kt").write_text(
        "package feature.collection\n"
        "import androidx.tracing.trace\n"
        "\n"
        "class CollectionMapper {\n"
        "    fun mapEntities(raw: List<Raw>) = trace(\"collection_mapping\") {\n"
        "        raw.map { it.toDomain() }\n"
        "    }\n"
        "}\n", encoding="utf-8")

    java = root / "app/src/main/java"
    java.mkdir(parents=True)
    (java / "Startup.java").write_text(
        "package app;\n"
        "import android.os.Trace;\n"
        "public class Startup {\n"
        "    public void initGraph() {\n"
        "        Trace.beginSection(\"di_graph_init\");\n"
        "    }\n"
        "    public void dynamic(String tag) {\n"
        "        Trace.beginSection(tag);\n"
        "    }\n"
        "}\n", encoding="utf-8")

    # The trap: a logging function with the same name but no tracing import.
    (java / "Noise.kt").write_text(
        "package app\n"
        "fun handle() { trace(\"this is a log line, not instrumentation\") }\n",
        encoding="utf-8")

    # The build directory must not be scanned.
    build = root / "app/build/generated"
    build.mkdir(parents=True)
    (build / "Gen.kt").write_text(
        "import androidx.tracing.trace\n"
        "fun g() { trace(\"generated_noise\") }\n", encoding="utf-8")


@check("domains: names, modules and hints are gathered from the sources")
def _(report):
    from . import domains as dm
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _sample_repo(root)
        sites, _ = dm.scan(root)
        found = {s.name: s for s in sites}

    assert set(found) == {"collection_mapping", "di_graph_init"}, sorted(found)
    assert found["collection_mapping"].module == ":feature:collection"
    assert found["di_graph_init"].module == ":app"
    # The hint must lead into the method, not the class: otherwise it is
    # useless in a file with two dozen methods.
    assert found["di_graph_init"].symbol == "method initGraph", found
    assert found["collection_mapping"].symbol == "fun mapEntities", found


@check("domains: a logger named trace is not mistaken for instrumentation")
def _(report):
    from . import domains as dm
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _sample_repo(root)
        names = {s.name for s in dm.scan(root)[0]}
    # Without an androidx.tracing import, a bare trace(...) is someone else's
    # function.
    assert "this is a log line, not instrumentation" not in names, names
    # And generated code is no place for hypotheses.
    assert "generated_noise" not in names, names


@check("domains: non-literal names are counted, not lost")
def _(report):
    from . import domains as dm
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _sample_repo(root)
        _, stats = dm.scan(root)
    # Trace.beginSection(tag) is visible in the trace but will never reach the
    # map — that has to be said out loud, or the gap looks like its absence.
    assert sum(s.dynamic for s in stats.values()) == 1, stats


@check("process mask: a wide match names a few alternatives, not hundreds")
def _(report):
    import contextlib
    import io
    from .main import _OTHERS_SHOWN, _resolve_process

    class _FakeTP:
        # 641 processes, as `*` gives on a real device: the chosen one plus
        # six hundred others that must not become a fifteen-kilobyte line.
        def query(self, sql):
            return [{"upid": i, "pid": 1000 + i, "name": f"proc{i}",
                     "slices": 1000 - i} for i in range(641)]

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        procs = _resolve_process(_FakeTP(), "*")
    assert len(procs) == 641, "every candidate is still returned to the caller"
    line = err.getvalue()
    assert "matched 641 processes" in line, line
    assert f"and {641 - 1 - _OTHERS_SHOWN} more" in line, line
    assert len(line) < 400, f"the warning is {len(line)} chars long"


# --- mark ------------------------------------------------------------------

def _mark_repo(root: Path, *, app="GloomApp", act="MainActivity", theme="AppTheme",
               nav="AppNavHost", pkg="com.example.app", app_return=False,
               launcher=True, second_app=False) -> None:
    """A Kotlin + Compose app: manifest, Application, launcher Activity with
    setContent, a theme and a nav host in two modules, Room in a core module.

    Every name is a parameter so the same tree can be built with nonsense
    names — `mark` must give the same skeleton, source for source.
    """
    (root / "settings.gradle.kts").write_text("", encoding="utf-8")
    for module in ("app", "feature/home", "core/database", "core/ui"):
        (root / module).mkdir(parents=True, exist_ok=True)
        (root / module / "build.gradle.kts").write_text(
            f'android {{ namespace = "{pkg}{"" if module == "app" else "." + module.replace("/", ".")}" }}\n',
            encoding="utf-8")
    main = root / "app/src/main"
    (main / "kotlin/x").mkdir(parents=True)
    filt = ("<intent-filter><action android:name=\"android.intent.action.MAIN\" />"
            "<category android:name=\"android.intent.category.LAUNCHER\" /></intent-filter>"
            if launcher else "")
    (main / "AndroidManifest.xml").write_text(
        f'<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        f'  <application android:name=".{app}">\n'
        f'    <activity android:name=".ui.{act}" android:exported="true">{filt}</activity>\n'
        f'    <activity android:name=".ui.Other" />\n'
        f'  </application>\n</manifest>\n', encoding="utf-8")
    ret = "        if (BuildConfig.DEBUG) return\n" if app_return else ""
    (main / f"kotlin/x/{app}.kt").write_text(
        f"package {pkg}\n\nclass {app} : Application() {{\n"
        f"    override fun onCreate() {{\n        super.onCreate()\n{ret}"
        f"        // a string with a brace: \"}}\" and a comment }} here\n"
        f"        init()\n    }}\n}}\n", encoding="utf-8")
    (main / f"kotlin/x/{act}.kt").write_text(
        f"package {pkg}.ui\n\nclass {act} : ComponentActivity() {{\n"
        f"    override fun onCreate(savedInstanceState: Bundle?) {{\n"
        f"        super.onCreate(savedInstanceState)\n"
        f"        setContent {{\n            {theme} {{\n                Surface(modifier = Modifier) {{\n"
        f"                    {nav}(start = true)\n                }}\n            }}\n        }}\n"
        f"    }}\n}}\n", encoding="utf-8")
    ui = root / "core/ui/src/main/kotlin"
    ui.mkdir(parents=True)
    (ui / "Theme.kt").write_text(
        f"package {pkg}.core.ui\n\n@Composable\nfun {theme}(content: @Composable () -> Unit) {{\n"
        f"    MaterialTheme(content = content)\n}}\n", encoding="utf-8")
    home = root / "feature/home/src/main/kotlin"
    home.mkdir(parents=True)
    (home / "Nav.kt").write_text(
        f"package {pkg}.feature.home\n\n@Composable\nfun {nav}(start: Boolean) {{\n"
        f"    NavHost(startDestination = \"home\") {{ }}\n}}\n", encoding="utf-8")
    db = root / "core/database/src/main/kotlin"
    db.mkdir(parents=True)
    (db / "Db.kt").write_text(
        f"package {pkg}.core.database\n\nfun open(ctx: Context) =\n"
        f"    Room.databaseBuilder(ctx, AppDb::class.java, \"app.db\").build()\n",
        encoding="utf-8")
    if second_app:
        wear = root / "wear/src/main"
        (wear / "kotlin").mkdir(parents=True)
        (root / "wear/build.gradle.kts").write_text(
            f'android {{ namespace = "{pkg}.wear" }}\n', encoding="utf-8")
        (wear / "AndroidManifest.xml").write_text(
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '  <application><activity android:name=".WearActivity">'
            '<intent-filter><action android:name="android.intent.action.MAIN" />'
            '<category android:name="android.intent.category.LAUNCHER" /></intent-filter>'
            '</activity></application>\n</manifest>\n', encoding="utf-8")
        (wear / "kotlin/WearActivity.kt").write_text(
            f"package {pkg}.wear\nclass WearActivity : ComponentActivity() {{\n"
            f"    override fun onCreate(b: Bundle?) {{ super.onCreate(b) }}\n}}\n",
            encoding="utf-8")


def _shape(pl) -> list[tuple[str, str, bool]]:
    return [(p.kind, p.source, p.applicable) for p in pl.proposals]


@check("mark: the skeleton comes from the platform's vocabulary, not the project's names")
def _(report):
    from . import mark as mk
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root)
        pl = mk.plan(root, package="com.example.app",
                     allowed=["app/src/main", "feature/home/src/main"])
        assert pl.module == ":app" and not pl.ambiguity, (pl.module, pl.ambiguity)
        shape = _shape(pl)
        assert shape == [
            ("app_oncreate", "manifest+lifecycle", True),
            ("activity_oncreate", "manifest+lifecycle", True),
            ("set_content", "api", True),
            # composables in call order: theme first, nav host inside it —
            # the theme sits in core/ui, outside allowed
            ("compose_root", "call-from-setContent", False),
            ("compose_root", "call-from-setContent", False),
            ("room_open", "api", False),
        ], shape
    by = {p.marker: p for p in pl.proposals}
    assert by["AGENTTMP_compose_AppTheme"].file == "core/ui/src/main/kotlin/Theme.kt"
    assert "outside instrumentation.allowed" in by["AGENTTMP_compose_AppTheme"].reason
    assert by["AGENTTMP_compose_AppNavHost"].file == "feature/home/src/main/kotlin/Nav.kt"
    assert by["AGENTTMP_room_open"].file == "core/database/src/main/kotlin/Db.kt"
    # the string "}" and the comment "}" in onCreate did not confuse the brace match
    assert by["AGENTTMP_app_oncreate"].applicable and by["AGENTTMP_app_oncreate"].close_at
    assert any("runtime-tracing" in n for n in pl.notes), pl.notes

    # the same tree, every name nonsense: same skeleton, source for source
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root, app="Zq1", act="Yx2", theme="Wv3", nav="Ut4", pkg="a.b.c")
        pl2 = mk.plan(root, package="a.b.c", allowed=["app/src/main", "feature/home/src/main"])
        assert _shape(pl2) == shape, (_shape(pl2), shape)
        # and it says so in the markers, with the project's own words
        assert {p.marker for p in pl2.proposals} >= {"AGENTTMP_compose_Wv3", "AGENTTMP_compose_Ut4"}


@check("mark: what it cannot see it says — no launcher, two launchers, an early return")
def _(report):
    from . import mark as mk
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root, launcher=False)
        pl = mk.plan(root)
        assert not pl.proposals and any("no launcher Activity" in n for n in pl.notes), pl.notes
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root, second_app=True)
        pl = mk.plan(root)   # no package to break the tie
        assert pl.ambiguity and not pl.proposals, (pl.ambiguity, pl.proposals)
        pl = mk.plan(root, package="com.example.app")
        assert pl.module == ":app" and not pl.ambiguity, (pl.module, pl.ambiguity)
        pl = mk.plan(root, module=":wear")
        assert pl.module == ":wear" and _shape(pl)[:1] == [("activity_oncreate", "manifest+lifecycle", True)], _shape(pl)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root, app_return=True)
        pl = mk.plan(root, package="com.example.app")
        app = next(p for p in pl.proposals if p.kind == "app_oncreate")
        assert not app.applicable and "return" in app.reason, app
    # the same tree twice: byte-identical
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root)
        a = "\n".join(mk.render(mk.plan(root, package="com.example.app")))
        b = "\n".join(mk.render(mk.plan(root, package="com.example.app")))
        assert a == b


@check("mark: --apply inserts tagged pairs, --remove restores the bytes")
def _(report):
    from . import mark as mk
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _mark_repo(root)
        before = {p: p.read_bytes() for p in mk.source_files(root)}
        pl = mk.plan(root, package="com.example.app", allowed=["app/src/main"])
        done = mk.apply(root, pl)
        files = {rel for rel, _ in done}
        assert files == {"app/src/main/kotlin/x/GloomApp.kt", "app/src/main/kotlin/x/MainActivity.kt"}, files
        act = (root / "app/src/main/kotlin/x/MainActivity.kt").read_text(encoding="utf-8")
        tagged = [ln for ln in act.splitlines() if mk.TAG in ln]
        assert len(tagged) == 4, tagged      # onCreate pair + setContent pair
        assert tagged[0].strip().startswith('android.os.Trace.beginSection("AGENTTMP_activity_oncreate")'), tagged
        # order inside the file: begin(onCreate) … begin(setContent) … end … end,
        # the inner pair indented one level deeper
        assert ["begin" in t for t in tagged] == [True, True, False, False], tagged
        assert "AGENTTMP_set_content" in tagged[1], tagged
        indent = [len(t) - len(t.lstrip()) for t in tagged]
        assert indent[0] == indent[3] < indent[1] == indent[2], indent
        # applying twice does not double the markers
        assert mk.apply(root, mk.plan(root, package="com.example.app", allowed=["app/src/main"])) == []
        touched = mk.remove(root)
        assert {rel for rel, _ in touched} == files and all(n == 2 or n == 4 for _, n in touched), touched
        after = {p: p.read_bytes() for p in mk.source_files(root)}
        assert after == before, "remove must restore every file byte for byte"


@check("domains: no instrumentation is a report, not an empty output")
def _(report):
    from . import domains as dm
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "build.gradle.kts").write_text("", encoding="utf-8")
        src = root / "app/src/main/kotlin"
        src.mkdir(parents=True)
        (root / "app/build.gradle.kts").write_text("", encoding="utf-8")
        (src / "Big.kt").write_text("fun a() {}\n" * 500, encoding="utf-8")
        sites, stats = dm.scan(root)
        text = "\n".join(dm.render(sites, stats, root))
    assert not sites
    assert "domains: []" in text
    assert ":app" in text and "500 lines" in text, text


# --- merging repeats -------------------------------------------------------

@check("merging repeats: median, not mean and not maximum")
def _(report):
    import copy
    from .report import aggregate
    first, second = copy.deepcopy(report), copy.deepcopy(report)
    second["trace"] = "second"
    for d in second["detectors"]:
        if d["id"] != "main_thread_block":
            continue
        for r in d["rows"]:
            if r["location"] == "collection_mapping":
                r["self_ms"] = 240.0        # twice as costly in the second run
        d["rows"].append({"location": "flaky_once", "count": 1,
                          "self_ms": 999.0, "total_ms": 999.0, "detail": "—"})

    merged = aggregate([first, second])
    rows = {r["location"]: r for r in next(
        d for d in merged["detectors"]
        if d["id"] == "main_thread_block")["rows"]}

    assert merged["runs"] == 2, merged["runs"]
    assert rows["collection_mapping"]["self_ms"] == 180.0, (
        f"the median of 120 and 240: {rows['collection_mapping']}"
    )


@check("merging repeats: a one-off finding is marked as one-off")
def _(report):
    import copy
    from .report import aggregate
    first, second = copy.deepcopy(report), copy.deepcopy(report)
    second["trace"] = "second"
    for d in second["detectors"]:
        if d["id"] == "main_thread_block":
            d["rows"].append({"location": "flaky_once", "count": 1,
                              "self_ms": 999.0, "total_ms": 999.0,
                              "detail": "—"})
    merged = aggregate([first, second])
    rows = {r["location"]: r for r in next(
        d for d in merged["detectors"]
        if d["id"] == "main_thread_block")["rows"]}
    # This is what repeats are for: 999 ms in one run out of two is not a
    # finding but an accident, and the report must tell it from a reproducible
    # one.
    assert rows["flaky_once"]["runs"] == "1/2", rows["flaky_once"]
    assert rows["collection_mapping"]["runs"] == "2/2", rows["collection_mapping"]


@check("merging repeats: a single trace stays itself")
def _(report):
    from .report import aggregate
    assert aggregate([report]) is report, "a needless wrapper on a single run"


# --- the .claude/ layer ----------------------------------------------------

@check(".claude/ layer: every part of the template is present")
def _(report):
    import json as _json
    from .main import CLAUDE_DIR
    required = [
        "skills/echolot/SKILL.md",
        "agents/perf-hunter.md",
        "commands/echolot-setup.md",
        "commands/echolot-hunt.md",
        "settings.json",
    ]
    for rel in required:
        assert (CLAUDE_DIR / rel).exists(), f"the template has no {rel}"
    # A broken settings.json quietly removes the permissions, and the agent
    # starts asking for confirmation on every call.
    _json.loads((CLAUDE_DIR / "settings.json").read_text(encoding="utf-8"))


@check(".claude/ layer: doctor tells stale from customised from current")
def _(report):
    import argparse
    import contextlib
    import io
    from .main import CLAUDE_DIR, LAYER_MANIFEST, cmd_init, layer_status, _write_manifest
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        assert layer_status(project) is None, "no layer yet must be None"
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_init(argparse.Namespace(into=str(project), force=False, no_doctor=True))
        assert (project / ".claude" / LAYER_MANIFEST).exists(), "init writes the manifest"
        st = layer_status(project)
        assert st and st["manifest"], st
        assert {r["state"] for r in st["rows"]} == {"current"}, st["rows"]

        skill = project / ".claude" / "skills" / "echolot" / "SKILL.md"
        agent = project / ".claude" / "agents" / "perf-hunter.md"
        # the project edits one file: customised, not stale
        skill.write_text(skill.read_text(encoding="utf-8") + "\n# ours\n", encoding="utf-8")
        # the template moves on for another. Stale means: the file equals
        # what init wrote and the template no longer does — emulated by
        # changing the installed copy and recording that as what init wrote.
        from .main import _sha
        agent.write_text(agent.read_text(encoding="utf-8") + "\n# older\n", encoding="utf-8")
        _write_manifest(project / ".claude", {"agents/perf-hunter.md": _sha(agent)})
        # and one file goes missing
        (project / ".claude" / "commands" / "echolot-hunt.md").unlink()

        by = {r["file"]: r["state"] for r in layer_status(project)["rows"]}
        assert by["skills/echolot/SKILL.md"] == "customised", by
        assert by["agents/perf-hunter.md"] == "stale", by
        assert by["commands/echolot-hunt.md"] == "missing", by
        assert by["settings.json"] == "current", by

        # init without --force keeps both edited files, restores the missing one
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cmd_init(argparse.Namespace(into=str(project), force=False, no_doctor=True))
        assert "≠ .claude/skills/echolot/SKILL.md (already there and customised" in out.getvalue(), out.getvalue()
        assert "+ .claude/commands/echolot-hunt.md" in out.getvalue(), out.getvalue()
        assert "# ours" in skill.read_text(encoding="utf-8")
        # --force overwrites and says which one had local edits
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cmd_init(argparse.Namespace(into=str(project), force=True, no_doctor=True))
        assert "! .claude/skills/echolot/SKILL.md" in out.getvalue(), out.getvalue()
        assert {r["state"] for r in layer_status(project)["rows"]} == {"current"}

        # a layer installed before the manifest existed: differs, not stale
        (project / ".claude" / LAYER_MANIFEST).unlink()
        skill.write_text("edited", encoding="utf-8")
        st = layer_status(project)
        assert not st["manifest"]
        by = {r["file"]: r["state"] for r in st["rows"]}
        assert by["skills/echolot/SKILL.md"] == "differs", by


@check("status: the next step follows the project's state, first visit to return")
def _(report):
    import argparse
    import contextlib
    import io
    from .main import NEXT_KINDS, cmd_init, next_kind, next_step, project_state
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        # nothing here yet: install the layer
        st = project_state(project)
        assert next_kind(st) == "init" and next_step(st).startswith("echolot init"), next_step(st)
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_init(argparse.Namespace(into=str(project), force=False, no_doctor=True))
        # layer, no config: build the config — /echolot does that
        st = project_state(project)
        assert next_kind(st) == "setup" and "/echolot" in next_step(st), next_step(st)
        (project / "echolot.yml").write_text(
            "project:\n  process: app\nscenario:\n  name: checkout\n", encoding="utf-8")
        # config, no traces: hunt (it collects), or collect by hand
        st = project_state(project)
        assert st["config"]["scenario"] == "checkout" and st["config"]["thresholds"] == "built-in defaults", st["config"]
        assert next_kind(st) == "hunt" and "collect" in next_step(st), next_step(st)
        # traces present: hunt or analyze
        (project / ".echolot" / "traces").mkdir(parents=True)
        (project / ".echolot" / "traces" / "checkout_iter000.perfetto-trace").write_bytes(b"x")
        st = project_state(project)
        assert st["traces"]["count"] == 1 and "analyze" in next_step(st), next_step(st)
        # a config that does not load is said, not swallowed
        (project / "echolot.yml").write_text("project: [\n", encoding="utf-8")
        st = project_state(project)
        assert st["config"] and st["config"].get("error"), st["config"]
        assert next_kind(st) == "fix-config" and next_step(st).startswith("fix echolot.yml"), next_step(st)
        # an open investigation nobody has touched for a while, with history
        # on disk: the human is asked rather than attached to it silently
        from . import hunt as hunt_mod
        (project / "echolot.yml").write_text(
            "project:\n  process: app\nscenario:\n  name: checkout\n", encoding="utf-8")
        hunt_mod.open_new(project, "checkout got slower", scenario="checkout")
        st = project_state(project)
        assert next_kind(st) == "hunt", "just opened — the same sitting, do not ask"
        aged = hunt_mod.load(project)
        aged["touched_at"] = "2020-01-01T00:00:00+00:00"
        hunt_mod.save(project, aged)
        st = project_state(project)
        assert next_kind(st) == "resume-or-new", next_kind(st)
        assert "carry on" in next_step(st), next_step(st)
        # and the loop is never asked: its own work keeps the hunt current
        hunt_mod.touch(project, analyze=True)
        assert next_kind(project_state(project)) == "hunt", \
            "the loop must never be interrupted by the choice"
        # every kind the skill switches on is one the decision can produce
        assert set(NEXT_KINDS) >= {"init", "init-force", "doctor", "setup",
                                   "fix-config", "resume-or-new", "hunt"}


@check(".claude/ layer: the skill knows every `next` the tool can produce")
def _(report):
    """A word `status --next` prints that SKILL.md does not list is a dead end.

    The skill dispatches on that one word. Adding a state to `next_kind` and
    forgetting the row leaves the agent holding a value it has no branch for,
    and what it does then is anyone's guess.
    """
    from .main import CLAUDE_DIR, NEXT_KINDS
    skill = (CLAUDE_DIR / "skills" / "echolot" / "SKILL.md").read_text(encoding="utf-8")
    missing = [k for k in NEXT_KINDS if f"`{k}`" not in skill]
    assert not missing, f"SKILL.md has no branch for: {missing}"


@check(".claude/ layer: the skill and the agent have frontmatter")
def _(report):
    from .main import CLAUDE_DIR
    for rel in ("skills/echolot/SKILL.md", "agents/perf-hunter.md"):
        text = (CLAUDE_DIR / rel).read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{rel}: no frontmatter"
        head = text.split("---", 2)[1]
        assert "description:" in head, \
            f"{rel}: no description — it will never trigger"


@check(".claude/ layer: reference links are not broken")
def _(report):
    import re as _re
    from .main import CLAUDE_DIR
    root = CLAUDE_DIR / "skills" / "echolot"
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    mentioned = set(_re.findall(r"references/([\w-]+\.md)", skill))
    assert mentioned, "the skill references no document at all"
    for name in mentioned:
        assert (root / "references" / name).exists(), (
            f"SKILL.md points at references/{name}, but the file is missing"
        )
    on_disk = {p.name for p in (root / "references").glob("*.md")}
    assert on_disk <= mentioned, (
        f"a reference exists but the skill does not know about it: "
        f"{on_disk - mentioned}"
    )


@check("docs: every document under docs/ is reachable, and no link dangles")
def _(report):
    """Reachable from the README or from the index the README points at.

    Two link forms have to count. The README is also the PyPI long
    description, where a relative path resolves against pypi.org, so its links
    are absolute; docs/README.md is only ever read on GitHub, so its links are
    relative. An earlier version of this check knew only the relative form and
    passed vacuously — it went green on a README that linked nothing at all.
    """
    import re as _re
    from .main import CLAUDE_DIR
    # The repo root, when we are running from a source checkout. An installed
    # package has neither README nor docs/ — nothing to verify there, so the
    # check passes trivially rather than failing for every user.
    root = CLAUDE_DIR.parent.parent
    readme = root / "README.md"
    index = root / "docs" / "README.md"
    if not readme.exists() or not (root / "docs").is_dir():
        return

    # (docs/name.md) and (https://github.com/…/docs/name.md) alike, plus the
    # index's own (name.md) written from inside docs/.
    def links(path, bare=False):
        if not path.exists():
            return set()
        text = path.read_text(encoding="utf-8")
        found = set(_re.findall(r"\((?:[^)\s]*/)?docs/([\w-]+\.md)\)", text))
        if bare:
            found |= set(_re.findall(r"\(([\w-]+\.md)\)", text))
        return found

    from_readme = links(readme)
    linked = from_readme | links(index, bare=True)
    assert from_readme or linked, "nothing under docs/ is linked from anywhere"
    for name in sorted(linked):
        assert (root / "docs" / name).exists(), (
            f"a link points at docs/{name}, but the file is missing"
        )
    # The index is the way in, not a document to be indexed by itself.
    on_disk = {p.name for p in (root / "docs").glob("*.md")} - {"README.md"}
    assert on_disk <= linked, (
        f"a document exists under docs/ but nothing links it: "
        f"{sorted(on_disk - linked)}"
    )
    if index.exists():
        assert on_disk <= links(index, bare=True), (
            f"the index does not list: {sorted(on_disk - links(index, bare=True))}"
        )


# --- the run ---------------------------------------------------------------

def build_report(tp_binary: str | None = None) -> dict:
    """Builds the fixture into a temp file and runs the detectors over it."""
    from .main import analyze_trace  # late import: main imports us

    with tempfile.TemporaryDirectory() as tmp:
        trace = Path(tmp) / "fixture.perfetto-trace"
        trace.write_bytes(fixture.build())
        return analyze_trace(trace, Config(FIXTURE_CONFIG), tp_binary)


def run(tp_binary: str | None = None) -> list[tuple[str, str | None]]:
    """[(check name, None if it passed else the mismatch text)]."""
    from . import recorder
    report = build_report(tp_binary)
    out = []
    # Checks call commands of their own (`init` into a temp dir); their notes
    # must not land in the log line of the doctor run that hosts them.
    with recorder.isolated():
        for name, fn in CHECKS:
            try:
                fn(report)
                out.append((name, None))
            except AssertionError as e:
                out.append((name, str(e)))
    return out
