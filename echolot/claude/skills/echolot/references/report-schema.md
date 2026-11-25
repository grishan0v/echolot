# The `report.json` schema

A report of five repeats merged, which is what `collect` and then `analyze`
produce. The keys only a merged report has are named under the block.

```json
{
  "schema": 2,
  "generated_at": "2026-08-15T12:00:00+00:00",
  "trace": "path of the first repeat",
  "traces": ["…/coldStart_iter000.perfetto-trace", "…"],
  "recorded_at": ["2026-08-15T11:52:03.118Z", "…"],
  "runs": 5,
  "scenario": "coldStart", "budget_ms": 2500,
  "verdict": { "level": "needs_attention", "budget_ms": 2500, "ratio": 3.1,
               "text": "The launch takes 7.7 s. You allowed 2.5 s, so it takes 3.1 times as long." },
  "toolchain": { "echolot": "0.18.0", "perfetto_package": "0.59.1",
                 "trace_processor": "v59.1", "source": "pinned", "binary": null },
  "window": { "ts_start": …, "ts_end": …, "duration_ms": 772.3,
              "duration_ms_min": 751.8, "duration_ms_max": 790.4,
              "values": [751.8, 768.0, 772.3, 781.5, 790.4],
              "process": "com.example.app", "pid": 4100,
              "start_anchor": { "glob": "bindApplication", "matches": 1 },
              "end_anchor":   { "glob": "…", "matches": 1 },
              "opened_inside": null,
              "main_thread": { "on_cpu": 380.1, "waiting_for_cpu": 30.6,
                               "in_kernel": 77.2, "sleeping": 282.1,
                               "other": 0.0, "accounted_ms": 770.0,
                               "window_ms": 772.3, "accounted_pct": 99.7,
                               "in_rows_ms": 401.6, "in_rows_pct": 52.0,
                               "in_rows_uncounted": [],
                               "runs": "5/5" },
              "crowding": { "threads": 135, "cores": 8, "peak": 27,
                            "above_cores_ms": 245.0, "runs": "5/5" } },
  "environment": { "cpu": { "mean_mhz": 1481.2, "mean_mhz_min": 1452.0,
                            "mean_mhz_max": 1503.9, "min_mhz": 300.0,
                            "max_mhz": 2400.0, "on_cpu_ms": 1945.0,
                            "measured_ms": 1902.4, "runs": "5/5" },
                   "thermal": { "max_celsius": 61.5, "hottest_zone": "cpu-therm",
                                "throttled": false, "throttle_device": null,
                                "throttled_runs": "0/5", "runs": "5/5" },
                   "memory": { "available_mb_min": 1536.0, "major_faults": 250 },
                   "sampling": null,
                   "compose_names": null,
                   "compile": { "filter": "speed-profile", "reason": "install-dm",
                                "status": "up-to-date",
                                "jit": { "methods": 7, "ms": 97.0 },
                                "verified": { "classes": 9, "ms": 68.0, "main_ms": 5.3 },
                                "runs": "5/5" },
                   "debuggable": { "repeats": 0, "of": 5 },
                   "missing": [] },
  "recording": { "runner": "launch", "reset_policy": "force-stop",
                 "app_at_stop": "running",
                 "animation_scales": { "animator": 1.0, "transition": 1.0, "window": 1.0 },
                 "always_finish_activities": false,
                 "data_sources": ["android.surfaceflinger.frametimeline",
                                  "linux.ftrace", "linux.process_stats",
                                  "linux.sys_stats"],
                 "atrace_categories": ["am", "binder_driver", "dalvik", "…"],
                 "ftrace_events": ["power/cpu_frequency", "sched/sched_switch", "…"],
                 "mixed": { "app_at_stop": [ { "value": "stopped", "runs": "1/5" },
                                             { "value": "running", "runs": "4/5" } ] } },
  "drift": { "runs": 5, "rows": 41, "drifts": null },
  "summary": { "detectors_run": 16, "detectors_fired": 4,
               "fired_ids": ["main_thread_block", "…"], "absent_ids": [] },
  "config": { "path": "/abs/project/echolot.yml", "sha": "fadc1a11b903",
              "local": null, "defaults": false, "set": null },
  "markers": { "prefix": "AGENTTMP_",
               "globs": ["AGENTTMP_*", "collection_mapping", "Screen.firstFrame"],
               "rows": [ { "location": "AGENTTMP_seed_first", "runs": "5/5",
                           "count": 1, "self_ms": 8.0, "total_ms": 60.0,
                           "max_ms": 60.0, "spread": { … },
                           "detail": "SeedWorker" } ],
               "absent": ["Screen.firstFrame"],
               "in_trace": ["AGENTTMP_seed_first"] },
  "detectors": [
    { "id": "…", "title": "…", "why": "…", "sha": "3f9c0d1e2a4b",
      "params": { … }, "params_source": "config",
      "defaults": { "min_slice_ms": 16 },
      "identity": ["location", "detail"],
      "attention": "needs_attention",
      "plain": { "title": "…", "why": "…", "clean": "…",
                 "found": "The heaviest piece is `draw`: 0.1 s of its own work, in all 5 launches. …",
                 "reason": null },
      "rows": [ { "location": "draw", "detail": "com.example.app",
                  "runs": "5/5", "count": 4,
                  "self_ms": 125.4, "total_ms": 130.1, "max_ms": 61.2,
                  "spread": { "self_ms": { "min": …, "max": …, "values": [ … ] },
                              "total_ms": { … }, "max_ms": { … } } } ],
      "error": null,
      "checked": true }
  ]
}
```

What the keys below the example mean is on this page for `traces` and `runs`,
`budget_ms`, `verdict`, `attention` and `plain`, `identity`, `config`,
`scenario` and `params_source`. The rest is a topic of its own:
`window.main_thread`, `in_rows_pct`, `crowding`, `frames`, `drawn` and
`startup` are `echolot guide report-window`; the anchors, `opened_inside`,
`defined_by`, `process_alternatives`, `drift`, `summary`, `recording`,
`toolchain`, a detector's `error` and `checked` are `echolot guide
report-checks`; `environment` is `echolot guide report-environment`;
`markers` is `echolot guide report-markers`; the columns of `rows`, with
`spread`, `code` and `places`, are `echolot guide report-columns`.

**`traces` and `runs`** — the repeats behind a merged report, by path, and
how many. One recording counts once however it was named: a path given twice,
a byte copy under another name or a symlink beside the file is read once, and
`same_recording` lists the paths that were one recording, the kept one first
(the markdown warns under the Traces line). `not_cold` lists the traces left
out because `collect` set them aside as not cold — stopped, and the process
running again by the start — each with its launch state; the medians are over
the others. `ended_elsewhere` lists the launches left out because they ended
on another activity than most of the set (`am start -W`'s `Activity:`, or the
trace's last `performCreate:`): another journey, such as an app opening the
page read last by itself; each has its `repeat`, its `activity` and the
`others`' one. `window.activities` counts them instead when as many ended on
each and none could be left out. `window.activity` is where a launch window's
launches ended, `window.alias` the activity a launcher alias stands for,
`window.handover` the splash's hand-over the window ran on through (`from`,
`to`, `first_frame_ms`, `after_ms`, `launches`, `runs`; see `echolot guide
report-checks`), and `window.boot_receivers` the app's `BOOT_COMPLETED`
receivers the main thread ran inside the window, with their time and `runs`:
on Android 15 every start after a force-stop runs them and a user's start does
not, so their time is the reset's, not something to place in the start's code. Every number in it is then a median across them, and a few keys say
how far the repeats spread: `window.duration_ms_min` and `_max`, with
`window.values` each repeat's window in the order of `traces` (what `compare`
tests the window's move with),
`window.main_thread.runs` (how many repeats had a main thread to account for),
`environment.cpu.mean_mhz_min` and `_max` with `environment.cpu.runs` (how many
had a clock to read), `environment.thermal.throttled_runs` — how many were
throttled, kept as a count because one throttled repeat is a fact about the
set rather than a vote — and `environment.sampling.runs` with
`runs_with_stack`, how many ran a callstack sampler and how many of those
got stacks. A report of a single trace has none of these keys.

**`budget_ms`, `verdict`, `detectors[].attention` and `detectors[].plain`** —
the report in plain words, for a person; a model writes none of it, and the
same report gives the same words. `budget_ms` is the config's
`scenario.budget_ms`, `null` when it sets none. `verdict` sets the window
against it: `level` is `fine` (within it), `worth_a_look` (up to a quarter
over), `needs_attention` (more), `no_limit` when there is no budget to
compare with, or `not_checked` when the window is not the scenario's (an
anchor matched nothing, `warning` says which); `ratio` is window over budget,
`text` the sentence. A detector's `attention` is one of `needs_attention`,
`worth_a_look`, `move_marker`, `fine` and `not_checked`: the first when one row
that is main-thread time holds at least 15% of the window and at least 150 ms in
every run (and always for an `anr` row), the second for any other row, the
third when the only rows are near misses of `repeated_work` (a marker one level
off, not a finding), the fourth for a silent detector whose entry
says `checked: true`, the last for a silent one that did not look (`checked:
false`, or a failure) or whose report is too old to say. A detector with rows
reads its rows, whatever `checked` says. `analyze` always writes `checked`, so an
entry without it is from before the key, and a silent one reads `not_checked`
with the reason "This report was written before echolot recorded whether a check
could run. Analyse the recordings again to find out."
`plain` holds the detector's three fixed texts from its `.sql` header —
`title` (what was checked), `why` (how it touches the app), `clean` (what a
silent result says) — and `found`, one sentence built from its rows, `null`
when it found nothing; `reason` says why a `not_checked` one did not run (the
entry's own, #441's). A report from before these keys has none of them, and
`echolot report --plain` works them out from the rows.

`echolot analyze --html` writes a third file, `report.html`, and
`echolot report --html` writes it for a report already on disk: the same
reading as a page for a person, one self-contained file. It holds nothing
`report.json` does not, so do not read it; when a human asks to see the report
as a page, write it and give them the path. A request that starts with
`/echolot-hunt` and lists rows as `- location · detector · ms, runs · evidence`
was copied from that page: the rows are the human's question, and the report
named on its last line is where they are from.

**`detectors[].identity`** — the columns that name a row of this detector:
`location`, and for several of them `detail` too. Merging repeats and
`compare` both pair rows on all of them, so two rows with one `location` and
different `detail` are two findings, not a duplicate. Where `detail` is part
of the identity it is the same in every repeat; elsewhere it is evidence,
taken from the repeat where the row cost most.

**`config`** — which file made this report, and a hash of what in it applies
to the report's scenario: every section, and of `scenarios:` that scenario's
block alone. A different `sha` means a value that applies changed — anchors,
process mask, thresholds, the runner — not a comment, a `_source` note, or
another scenario, and says nothing about `local.yml`, which is not hashed. The
thresholds each detector ran with are its `params`, and `compare` lists the
ones that differ in its `thresholds` warning. `defaults: true` means `--defaults` ran every detector with its built-in
numbers, `set` lists what `--set` overrode for that run. `mapping` is the
build's R8 mapping the config names, and `found: false` means the build had not
written it yet: R8's names stay in the report.

**`scenario`** — which of the config's scenarios the window is: the key under
`scenarios:`, or the `name` of a single `scenario:`. `compare` refuses two
reports whose scenarios differ. A report from before the key existed has none.

**`detectors[].params_source`** — where this detector's thresholds came from:
`default` (the numbers shipped in the .sql), or what gave them, joined by `+`
in this order: `config` (calibrated or hand-set in the top-level `detectors:`
of `echolot.yml`), `scenario` (the scenario's own `detectors:` under
`scenarios:`, the report's `scenario`), `cli` (`--set`) — `config+scenario`,
`scenario+cli` and the like. When it is not
`default`, `defaults` holds the shipped values for the overridden parameters,
so you can see how far the bar moved without running `explain`. A silent
detector with calibrated thresholds means "nothing above the calibrated
bar" — not necessarily "nothing above the default one".
