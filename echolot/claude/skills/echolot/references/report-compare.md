# The comparison — `echolot compare`

Two Marker Reports in, one table out, sorted by how far each row moved — a
move of the main thread's own time (or the frames') that holds and is a
twentieth of the window or more first, a move that does not hold last; each
row's `on_main` says whose time it is, and a pool's sum is not the scenario's
time. Written
to `comparison.json` and `comparison.md` beside the report when `echolot.yml`
is found; run from a folder without one, it prints the table and writes
nothing. `echolot compare --plain` prints the same comparison in plain words
for a person, `--html` writes it as a page, `comparison.html`, and either keeps
that reading in `comparison.json` under `plain`; the table is still what an
investigation reads.

```bash
echolot compare                      # inside an investigation: previous round vs latest
echolot compare --hunt 3             # its first report against its last
echolot compare old.json new.json    # or name them
```

```json
{
  "kind": "comparison",
  "comparable": true,
  "warnings": [ { "id": "thresholds", "text": "…" } ],
  "window": { "before_ms": 1184.0, "after_ms": 2960.4,
              "delta_ms": 1776.4, "ratio": 2.5, "change": "grew",
              "shift": { "ms": 1771.2, "low_ms": 1690.5, "high_ms": 1860.0,
                         "resolves_ms": 81.4, "runs_needed": null },
              "holds": true },
  "startup": { "type": { "before": "cold", "after": "cold" },
               "before_ms": 1240.0, "after_ms": 3012.5, "delta_ms": 1772.5,
               "change": "grew", "shift": { "…": "…" }, "holds": true,
               "reasons": [ { "reason": "Running", "before_ms": 402.0,
                              "after_ms": 1273.3, "change": "grew",
                              "shift": { "…": "…" }, "holds": true }, … ] },
  "summary": { "moved": 4, "appeared": 2, "vanished": 1, "steady": 17,
               "state_changed": [ { "id": "binder_txn",
                                    "before": "silent", "after": "1 row(s)" } ],
               "not_compared": [ { "id": "io_wait", "before": null,
                                   "after": "The recording does not carry …" } ] },
  "rows": [
    { "location": "TeamRepository.loadAll", "detector": "main_thread_block",
      "metric": "self_ms", "change": "grew", "matched_by": "exact",
      "before": { "self_ms": 12.1, "min": 10.4, "max": 14.0,
                  "values": [ … ], "count": 1, "runs": "5/5",
                  "detail": "com.example.app" },
      "after":  { "self_ms": 883.4, "min": 843.4, "max": 923.4,
                  "values": [ … ], "count": 1, "runs": "5/5",
                  "detail": "com.example.app" },
      "delta_ms": 871.3, "ratio": 73.01,
      "shift": { "ms": 871.3, "low_ms": 831.3, "high_ms": 911.3,
                 "floor_ms": 5.0, "resolves_ms": 40.0, "runs_needed": null,
                 "more_runs": null },
      "holds": true, "detail": "com.example.app" }
  ]
}
```

**`change`** is `appeared`, `grew`, `shrank`, `vanished` or `steady`. Rows are
sorted by the size of the move, so the first one is usually the answer. Steady
rows stay in the JSON and are collapsed to one line in the markdown; nothing is
dropped silently.

**`holds`** answers whether the repeats support calling it a change. `shift`
is the move with the range it lies in, 95% sure, worked out from every run
after paired with every run before. `true` means the whole range is past
`shift.floor_ms` — the floor that listed the row, 5 ms or a tenth of the
before value — on one side: the move survives a re-record, and the end nearer
zero is the least it moved. Not zero: a comparison judges about two hundred
rows at once, and against zero one build set against itself held four of them
a comparison; against the floor, half a row (`holds_against: "floor"` at the
top says so). `false` means the range reaches into the floor or runs through
zero, so the runs cannot tell the move from the floor or from nothing: record
another round before concluding. `null` in both means there was nothing to test: the row is on one
side only — every `appeared` and `vanished` row — or the row has too few runs
to be 95% sure. The count is the row's own: a row found in `2/5` runs has two
values, and gets `null` with no warning above it, while `before.runs` and
`after.runs` say why. For the report as a whole four repeats a side are always
enough, and below that the `single` or `few` warning says so.

`shift.resolves_ms` is how far these two sets' strays reach in the direction
this move went: `holds` is `true` exactly when the medians moved further than
the floor and that together. When they did not, `shift.runs_needed` says about
how many runs a side would settle a move this size, by the square root of the
runs and assuming the next runs stray as much as these did — an order of
magnitude, not a count — and `shift.more_runs` the fewest more on each side,
`{"before": 0, "after": 1}`: with fifteen runs before and four after, more
after alone is often the cheaper round. `null` with `runs_needed` over a
hundred means more rounds will not settle it — say the move is below what
this scenario can resolve, or bring the spread down first.

**`window`** and **`startup`** are tested as a row is: `change`, `shift`,
`holds`, from each run's window and startup the reports keep; `holds` asks
the range to clear zero, one number tested once. Read
`window.holds` before the table: `false` with a window that grew means the
runs do not settle that the launch got longer, whatever the rows say. Both are
`null` where a row's would be; `window.untested` names a side whose report is
too old to keep per-run windows (analyse it again). `startup` is null unless
both sides have one; its `reasons` are the platform's split of the launch,
judged as rows but not added to `rows` — the detectors already count that
time. `compare --plain` calls no window better or worse whose move does not
hold.

**`count` before and after** separates "called more often" from "became slower
inside". `inflate` at 12 → 31 occurrences and `loadAll` growing 73× at one
occurrence are different bugs in different places.

**`markers`** is the report's markers table paired across the two sides, in
the shape of `rows` with `detector: "markers"`: the project's own sections,
async ones included, which no detector reads. `calls` judges a marker's calls
per run on their own — `before`, `after`, `moved`, `holds` — so a load that ran
twice per run instead of once is a finding even when no detector row moved.
`summary.markers_moved` counts the ones that moved by time or calls; in
`comparison.md` they are the **Markers** section.

**`names`** is the slice names of the window that are in every run of one
side and in no run of the other — `after` and `before`, each with its `runs`,
the other side's `other` (`0/n`), `total_ms` a run, `max_ms` and `threads`.
No detector reads them, so no row moves for them, and they are often the one
sign of what a change did: a busy loop with no section of its own gave no row
on either side, and the one name only the slow build had was the JIT
compilation of the method the loop is in — `JIT compiling void
com.example.app.feature.ListViewModel.prepare() …`, which is where to look.
A new async section, binder call or thread shows up the same way. Numbers in
names, and the APK's install directory, are folded; a name the other side ran
for any time at all is not listed. Three runs a side at least, or `too_few`
says nothing was looked for. `comparison.md` shows ten a side as **Names on
one side only**; `echolot compare … --names` prints every one.

**`matched_by`** is `exact` or `family`. Rows are paired by name first and by
name family second — `arch_disk_io_0` and `arch_disk_io_3` are one pool.
The family pass only fires when exactly one row on each side is unmatched;
otherwise the rows stay listed as appeared and gone rather than guessed at.
A name carrying the config's `instrumentation.temp_prefix` keeps its digits in
that pass: `AGENTTMP_fill_v4` is never taken for `AGENTTMP_fill_v6`.

**`warnings`** — read before the table:

| `id` | what it means |
|---|---|
| `thresholds` | detector parameters differ. **appeared** and **gone** mean the bar moved, not that the app changed. Re-run both with `--defaults` |
| `environment` | the clock the two rounds ran at differs by the relative floor or more (10% unless `--floor-pct` says otherwise), either way, or a side carries no clock, or read its clock or thermal state in only some of its repeats. A grown row may be the device rather than the app — say so before calling it a regression. Two rounds that recorded no platform state at all get no warning |
| `environment-thermal` | the kernel throttled the device during one round and not the other: that side is slower for a reason outside the code. Only when both sides recorded thermal state |
| `environment-thermal-one-side` | the kernel throttled the device during one round, and the other has no thermal record at all. The throttled side is a slowed device; whether the other was too cannot be said — never call it cool |
| `recording` | the two sides were not recorded the same way: runner, reset policy, the app found running at the stop on one side and stopped on the other, the trace config (data sources, atrace categories, ftrace events), or the device's animation scales or "Don't keep activities". The detectors that read what one side recorded alone are named. Rows can move for that reason alone — say it before calling one a regression |
| `recording-mixed` | one side's repeats were not recorded alike (runner, reset policy, trace config or the device's settings): its medians mix two ways of recording |
| `recording-unknown` | one side cannot say how it was recorded and the other can — older traces or report, traces not from `collect`, or a `collect` from before it read the device's settings. Unknown, not "the same" |
| `version` | the reports come from different echolot versions, or one does not say which. A side that does not say leaves every row without a verdict: analyze its traces again |
| `detector-changed` | the named detectors read the trace with different code on the two sides; their rows carry no verdict, a move there may be the tool's |
| `same-traces` | both sides are one set of recordings read twice (same trace names, pid and window): every move is the tool's or the config's, and no row carries a verdict |
| `drift` | the runs of one side, or both, drift from the first to the last (that side's `drift.drifts`): its medians are no one state of the device, and a row that drifts with it can hold with nothing changed. Named with each side's `p` and the rows that follow the order closest. The verdicts stand; before acting on a row that holds, record the two sides back to back and compare again. A report from before the block is read from its rows |
| `environment-load` | other processes took 40% or more of the CPUs' time in one round's window, 15 points or more above the other. That side ran on a busy device; the warning names the largest load. Record it again with nothing else running. The processes that draw the app are not counted (`platform_pct`), and a report from before they were left out gets no warning against one after |
| `sampling` | a callstack sampler ran in one round and not the other, or at another rate, or in only some repeats of one. The sampled side is slower for a reason outside the code. A round whose sampler never started counts as plain, and a report from before the field gets no warning |
| `compile` | the two rounds ran the app compiled differently — another `filter`, or the same filter from another profile (`reason`) — or one side's repeats did. Code compiled ahead of time runs faster than code the JIT compiles as the app runs, so a row can move for that reason alone. A side whose traces held no state (the process was already running) is judged by its JIT count instead: three times the other side's methods or more, and 50 or more apart, warns. A report from before the field gets no warning |
| `instrumentation-removed` | temporary markers that are gone were taken out between the rounds, not work that went away; the slices they sat in got their self time back and are judged on their total |
| `instrumentation` | markers that appeared, each named once, carry the config's `instrumentation.temp_prefix` — markers added between the rounds, a breakdown of a blind spot, not new work. Needs that key in the config |
| `process` | two different apps. `comparable: false`, and every `holds` is `null` |
| `window` | the two windows were not defined the same way: anchors against a launch window, a launch closed at its end against one that ran on to `reportFullyDrawn()`, or a side whose repeats did not agree. `comparable: false`. The command refuses the pair before any table is made; the warning is what another caller of the comparison sees |
| `defaults` / `config` | one side used `--defaults` and that changed a threshold or which checks ran (not said when the config sets no thresholds), or the config's hash changed; what the reports show changed (an anchor's glob, the config's path) is named |
| `anchor-before` / `anchor-after` | that side's start or end anchor never matched in any repeat: its window runs to the edge of the trace, `comparable: false` and every `holds` is `null`. With no launch in those traces either, the runner (`runner.reset_policy`) is named rather than the config. Missed in some repeats only (`not found in 1 of 15 after repeats`): still comparable, and the remedy is to leave those traces out — the warm ones, when the set mixes cold and warm starts |
| `frames` | one side drew fewer frames in the window, by the relative floor or more. Rows whose count followed the frames carry `per_occurrence` (time per occurrence each side) and `holds: null`: their total moved with the frames, not with the work. The rows that moved against the frames come first. Read `per_occurrence`, and the app's own late frames, before calling anything faster or slower |
| `runs` / `single` | different repeat counts, or a single trace with no spread to test against |
| `few` | too few repeats to be 95% sure of any move — four a side is always enough — so every `holds` is `null`. Record another round rather than read the moves as settled |
| `detectors` | the two runs did not use the same set of detectors |
| `detector-failed` | a detector failed on that side. Its rows on the other side are left out rather than called gone or new; the failure is no answer about the app |
| `not-checked-before` / `not-checked-after` | detectors that could not run on that side, because its recording or its window does not hold what they read (`checked: false` in its report). Their rows are left out on both sides rather than called gone or new, and `summary.not_compared` gives each side's reason, `null` for a side that could look. Record that side again with what they read; a window's reason, no start of the app's process, is the scenario's, and no recording changes it |
| `not-checked-both` | the same detectors could not run on either side, for the same reasons: one warning in place of the two above. They are left out of the comparison |

A row is listed as moved when it changes by more than 5 ms or 10 %, whichever
is larger — `--floor-ms` and `--floor-pct` change that. A row whose share of
repeats differs between the sides — `120.0 (1/5)` before, `(5/5)` after —
counts each repeat without it as zero, so work that now happens on every run
is listed as grew. The exit code is 0
whatever the comparison finds.
