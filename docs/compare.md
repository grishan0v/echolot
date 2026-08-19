# Comparing two reports

[← back to the documentation index](README.md)

`echolot analyze` answers where the time went in one set of traces. The
question people actually arrive with has two halves — *it was 3 s, now it is
7 s, where did that go* — and the second half needs two sets.

`echolot compare` takes two Marker Reports and returns one table, sorted by how
far each row moved. The top row is usually the answer.

```bash
echolot compare                       # the open investigation: previous round vs latest
echolot compare --hunt 3              # investigation 3: its first report vs its last
echolot compare old.json              # that report vs .echolot/out/report.json
echolot compare old.json new.json     # exactly those two
```

It writes `comparison.md` and `comparison.json` next to the report, by the same
rule `analyze` uses: a relative `-o` is taken from the config's directory, so
running it from a build folder full of traces still lands the output in the
project. Without a config it prints to stdout and writes nothing.

## What the table says

```markdown
| Where | Detector | Before | After | Δ | N | Ranges |
|---|---|---|---|---|---|---|
| SyncAdapter.onPerformSync | uninstrumented_cpu | — | 1402.0 ±61 | **new** | — → 0 | — |
| TeamRepository.loadAll | main_thread_block | 12.1 ±2 | 883.4 ±40 | **+871.3 ×73** | 1 → 1 | apart |
| inflate | main_thread_block | 47.3 ±31 | 121.9 ±88 | +74.6 ×2.6 | 12 → 31 | overlap |
```

**One table, not one section per detector.** The Marker Report is grouped by detector
because each answers a different question. A comparison has one question, so
the detector moves into a column and the rows are sorted across all of them.

**The measure is each detector's own.** Self time where a detector reports it,
total time otherwise — the same rule the report itself sorts by, so the number
in the Δ column is the number the report ranked on.

**`N` is not decoration.** `inflate` doubling at 12 → 31 occurrences means it
is being called more often. `TeamRepository.loadAll` growing 73× at one
occurrence means it became slower inside. Those are different bugs in different
places, and the millisecond column alone cannot tell them apart.

**`±` is the furthest a repeat strayed from the median**, not a standard
deviation. Exact bounds and the per-run values are in `comparison.json`.

## The Ranges column

This is the column that decides whether a row is worth acting on.

| value | meaning |
|---|---|
| `apart` | every repeat after fell outside everything seen before — the move survives a re-record |
| `overlap` | the ranges intersect: some run before was already as slow as some run after |
| `—` | one side had a single trace, so there is nothing to test against |

`overlap` does not mean the row is uninteresting. It means the repeats disagree
among themselves by more than the medians moved, and the honest next step is
another round of `collect` rather than a conclusion.

This works because `analyze` keeps the per-run values when it merges repeats —
see [`spread`](#what-analyze-keeps-for-it) below. With one trace on each side
the column is empty for every row, and the comparison is a difference of two
single measurements.

## What is a move at all

A row is listed when it moves by more than **5 ms or 10 %**, whichever is
larger. Both halves earn their place: the absolute floor stops a 4 ms wobble on
a 6 ms slice reading as "×1.7 slower", and the relative floor stops a 40 ms move
on a 900 ms slice reading as a finding.

```bash
echolot compare --floor-ms 20 --floor-pct 25    # only the large moves
```

Everything below the floor is counted in the summary and listed on one line
under **Steady**. Nothing is dropped silently.

## The same thing under a new name

Thread pools hand work to whichever worker is free, so
`DefaultDispatcher-worker-2` in one set and `-worker-5` in the next is one
phenomenon under two names. Matching on the literal name reports a large row
gone and an unrelated large row appeared — twice wrong, in the two places it
matters most.

So rows are paired in two passes: exact name first, then by name family, which
collapses digits and hex the way `echolot names` does when it builds its
inventory.

The second pass only fires when the family is unambiguous — exactly one
unmatched row on each side. With two workers before and three after there is no
honest way to say which became which, and they stay listed as appeared and
gone. A row paired this way is marked `*(family)*` in the table and
`"matched_by": "family"` in the JSON.

## When two reports may not be compared

Subtraction always produces a number, including for two reports that have
nothing to do with each other. Nothing here refuses to subtract — a table with a
reason above it is more useful than an error — but every reason is stated at the
top, the way the Marker Report already states an anchor that never matched.

| warning | why it matters |
|---|---|
| `process` | two different apps. `comparable: false`; nothing below is a comparison |
| `thresholds` | detector parameters differ. **appeared** and **gone** mean "the bar moved", so they say nothing about the app. Named parameter by parameter, with both values |
| `defaults` | one side ran with `--defaults` and the other did not |
| `config` | the config's hash changed between the two: anchors, process mask and thresholds all live there |
| `anchor-before` / `anchor-after` | that side's window is the whole trace rather than the scenario |
| `runs` | different numbers of repeats; the narrower range is the smaller sample |
| `single` | one trace on a side: no spread, so the Ranges column is empty throughout |
| `detectors` | the two runs did not use the same set of detectors |

The one to read first is `thresholds`. After `echolot calibrate` the numbers in
`echolot.yml` are derived from particular runs, and comparing a calibrated
report against a default one produces a page of rows that appeared and vanished
without anything in the app changing. Re-run both sides with `--defaults` when
that happens.

## What `analyze` keeps for it

Merging repeats used to reduce every row to a median, and a median cannot say
whether a number is steady: 120 ms from (118, 119, 121) and 120 ms from
(12, 120, 890) read identically, and only the second one means the next run will
say something else.

So `analyze` now keeps the per-run values for two columns — the detector's
ranking metric and `max_ms` — under `spread`:

```json
{ "location": "draw", "runs": "5/5", "self_ms": 125.4,
  "spread": { "self_ms": { "min": 118.2, "max": 340.1,
                           "values": [118.2, 121.0, 125.4, 133.7, 340.1] } } }
```

Two columns rather than all five, because the report staying small is the point
of it. The ranking metric because every conclusion is drawn from it, and
`max_ms` because that is where a single slow occurrence shows up at all — and a
median over maxima across repeats is exactly what hides one.

`values` holds one entry per repeat **the row was found in**, which is what the
`runs` column counts: a row with `3/5` has three values, not five. `report.md`
is unchanged — this lives in the JSON, where the readers that need it are.

## The comparison JSON

```json
{
  "schema": 1,
  "kind": "comparison",
  "comparable": true,
  "warnings": [ { "id": "thresholds", "text": "…" } ],
  "before": { "path": "…", "runs": 5, "generated_at": "…",
              "config_sha": "a3f9c21b", "defaults": false },
  "after":  { "…": "the same shape" },
  "window": { "before_ms": 1184.0, "after_ms": 2960.4,
              "delta_ms": 1776.4, "ratio": 2.5 },
  "noise_floor": { "abs_ms": 5.0, "ratio": 0.1 },
  "summary": { "moved": 4, "appeared": 2, "vanished": 1, "steady": 17,
               "fired_before": [ … ], "fired_after": [ … ],
               "state_changed": [ { "id": "binder_txn",
                                    "before": "silent", "after": "1 row(s)" } ] },
  "rows": [
    { "location": "TeamRepository.loadAll", "detector": "main_thread_block",
      "metric": "self_ms", "change": "grew", "matched_by": "exact",
      "before": { "self_ms": 12.1, "min": 10.4, "max": 14.0,
                  "values": [ … ], "count": 1, "runs": "5/5" },
      "after":  { "self_ms": 883.4, "min": 840.1, "max": 931.7,
                  "values": [ … ], "count": 1, "runs": "5/5" },
      "delta_ms": 871.3, "ratio": 73.0, "overlap": false }
  ]
}
```

`change` is one of `appeared`, `grew`, `shrank`, `vanished`, `steady`, so an
agent can take the rows worth looking at without parsing any numbers:

```
rows[?change == 'appeared' || (change == 'grew' && overlap == false)]
```

`overlap: false` means the ranges are apart. `null` means one side had a single
trace and there was nothing to test.

## In CI

The [README explains](../README.md#there-is-no-ci-gate-on-purpose) why `analyze`
does not fail a build against a budget. A comparison is the other shape, and it
is the one worth having: run `analyze` over the traces the benchmark already
wrote, compare against yesterday's `report.json`, and keep `comparison.json` as
a build artefact.

The exit code is 0 whatever the comparison says. This command reports; it does
not stand guard.

When someone asks a day later why the nightly regressed, the answer is already
sitting next to the commit — with the window, the thresholds, the evidence and
now the delta.

## Related

- [Collecting traces](collecting.md) — why repeats, and why the sets must be repeats of one scenario
- [Calibrating](calibrate.md) — where the threshold warning comes from
- [Analysing](analysing.md) — from a row in the table to a place in the code
