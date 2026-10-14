# Thresholds from a healthy run

[← Docs index](README.md) · [README](../README.md)

An absolute threshold is brittle. 16 ms on a flagship and on a budget phone are
different things, and the config does not travel between devices. Worse, it
does not travel between scenarios either: on a 772 ms startup and on a minute
of gameplay, "long" means something entirely different.

```bash
echolot calibrate run1.perfetto-trace run2.perfetto-trace -c echolot.yml
```

The detectors run with their thresholds **opened up**, a statistic is taken per
report column on each trace, the median across the traces stands for the set,
then a safety factor is applied. The output is a ready `detectors:` section
with the reasoning on every line:

```yaml
detectors:
  frame_jank: false
  main_thread_block:
    min_slice_ms: 26.6    # top10(self_ms)=17.7 × 1.5, median of 5 run(s), sample 175 per run
  runnable_starvation:
    min_runnable_ms: 27.2    # top10(total_ms)=18.1 × 1.5, median of 5 run(s), sample 46–50 per run
    skip_glob: "*Binder*"    # from the config
```

The section can replace the config's whole. A detector the config turned off
comes back as `false` and is not measured; whatever the config sets that is
not calibrated — a mask, a ratio, a detector with no `@calibrate` — is carried
over as it stands; and a threshold left uncalibrated keeps the config's value
as a setting, or the shipped one as a comment.

The command deliberately **does not edit the config itself**. Thresholds define
what counts as normal, and that decision is not handed to a script: it prints a
proposal, a human looks at the numbers and decides.

## How a detector declares its calibration

Next to the threshold, in the header:

```sql
-- @param: min_slice_ms = 16
-- @calibrate: min_slice_ms = top10(self_ms) * 1.5
```

The measuring pass zeroes the calibrated thresholds so `HAVING` lets everything
through, and strips the trailing `LIMIT` — a statistic over a truncated top
twenty would be a statistic over the tail.

Only thresholds that zero **opens** can be calibrated. A ratio like
`max_covered_pct` is not: 50% stays 50% on any device, and zeroing it would
shut the filter completely.

## Why rank rather than percentile

Both forms are supported:

```sql
-- @calibrate: min_slice_ms = top10(self_ms) * 1.5
-- @calibrate: min_slice_ms = p95(self_ms) * 1.5
```

The default is `topN`, "the Nth largest value". It reads as *on a healthy run
this detector should produce no more than N rows*, which sets the report size
directly. That is why it is taken per run: `analyze` folds the repeats into one
row per identity, so a report is the size of one run. Over the rows of five
traces pooled, each value came five times, `top10` landed near the second
largest of one run, and the threshold grew with the number of repeats — on the
demo app's cold starts, 4.7 from one trace and 170.3 from five.

A percentile behaves worse on live traces, because it depends on the size of
the population — and that jumps around:

| scenario | distinct slice names | `p95` | `top10` |
|---|---|---|---|
| cold start | 175 | 26.9 ms | 26.6 ms |
| a minute of gameplay | 4729 | **0.8 ms** | 300.8 ms |

With 4729 groups, more than two hundred rows still sit above p95 — the
threshold stops being a threshold. A rank does not care how large the
population is.

## When no threshold is derived

Two cases, and both are more honest than a number.

**The sample is below `--min-sample`** (10 by default) in every run. A
statistic over a handful of values is a random number wearing the look of a
justified one. The sample is counted per run, as the statistic is taken; a run
short of it is left out of the median.

**A degenerate tail.** The sample is large enough, but the Nth value is already
near zero. A threshold of zero means "report everything", which is not a
threshold — there is nowhere for a number to come from when a healthy run
barely feeds this detector.

In both cases the output keeps a comment with the default and the reason, plus
a summary line at the end.

A detector whose query **failed** is neither: its error is on stderr, its line
says it failed on every trace, or on how many, and the summary names it. When
it failed on every trace, `calibrate` exits 1.

## Calibrate on repeats of one scenario

Mixing a cold start with a minute of scrolling yields thresholds for nothing.
The command warns when the windows diverge more than twofold, but assembling
comparable runs is the job of `collect`.

What that looks like in practice, from the same app on two devices:

```
                       emulator   Galaxy A51
runnable_starvation        27.2          7.9
main_thread_block          26.6         36.5
binder_txn total           84.3         30.5
```

Two thresholds fell about threefold and one rose by about a third. One config with absolute
numbers would have meant something completely different on those two machines —
which is the entire argument for this command.

## Several scenarios

Thresholds do not travel between the journeys of one app either. Two of
them, recorded on one Galaxy A51 from one version of the code, its cold start
and its first launch after `pm clear`, calibrated like this:

```
                       cold start   first launch
runnable_starvation           3.6            9.8
uninstrumented_cpu           10.4           27.3
binder_txn total              1.1            2.5
main_thread_block            17.8           21.5
```

Analysed with the first launch's numbers, the cold start lost 9 of its 12
`runnable_starvation` rows and 6 of its 8 `uninstrumented_cpu` rows. The first
launch, with the cold start's, listed 30 and 20 rows instead of 13 and 7, all
of them its normal run.

So in a config with `scenarios:`, each scenario can have a `detectors:` of
its own, laid over the top-level one for that scenario alone, a threshold at
a time. What the scenario names wins for it, what it leaves out comes from the
top, `false` turns a detector off for that scenario, and thresholds turn on
one the top turned off. The top-level section is the place for what every
scenario shares: masks, switches.

With `scenarios:`, `calibrate` prints the section of the scenario it
measured, to paste under that scenario beside its `start` and `end`:

```yaml
scenarios:
  cold_start:
    detectors:
      main_thread_block:
        min_slice_ms: 17.8    # top10(self_ms)=11.9 × 1.5, median of 5 run(s), sample 246–254 per run
      main_thread_outlier:
        min_abs_ms: 40    # kept the default over the 2.8 in detectors: — top10(max_ms)=0.15, the tail of the distribution is degenerate
```

It carries over what the scenario's own section holds and leaves the
top-level one where it is. One line needs saying. A threshold the scenario's
runs gave no number for, which the top-level section sets, comes back as the
default, written down as a setting. Left out, the top's number would reach
the scenario through the overlay: above, the cold start would have run on the
first launch's 2.8 ms where its own runs supported only the default 40.
Delete the line when the top's number is one you set for every scenario.

A report says whose numbers it ran on: `params_source` is `scenario` for a
detector whose thresholds came from the scenario's own section, and the
`Config:` line in `report.md` reads ``thresholds: scenario `cold_start`'s own
for 5 of 14 detectors``. `echolot status` marks the scenarios with thresholds
of their own. A config with one `scenario:` keeps its thresholds in the
top-level `detectors:`.

## Calibrate on healthy runs, not on the runs under investigation

The word "healthy" carries the whole method. Thresholds derived from the ten
traces that hold the regression sit *above* the regression, and `analyze`
against them reports a clean run — the bar was set on the problem. Calibrate
on a build known to be good; when there is none, leave the defaults in place
and hunt with those.

To see what the shipped numbers would say without touching the config, run
`analyze --defaults`; to move one threshold for one run,
`analyze --set main_thread_block.min_slice_ms=16`. Both are recorded in the
report (`config.defaults`, `config.set`, `detectors[].params_source`), so a
report made that way is not mistaken for the project's.
