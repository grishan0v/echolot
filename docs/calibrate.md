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
report column, then a safety factor is applied. The output is a ready
`detectors:` section with the reasoning on every line:

```yaml
detectors:
  main_thread_block:
    min_slice_ms: 26.6    # top10(self_ms)=17.7 × 1.5, sample 175
  runnable_starvation:
    min_runnable_ms: 27.2    # top10(total_ms)=18.1 × 1.5, sample 48
```

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
directly.

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

**The sample is below `--min-sample`** (10 by default). A statistic over a
handful of values is a random number wearing the look of a justified one.

**A degenerate tail.** The sample is large enough, but the Nth value is already
near zero. A threshold of zero means "report everything", which is not a
threshold — there is nowhere for a number to come from when a healthy run
barely feeds this detector.

In both cases the output keeps a comment with the default and the reason, plus
a summary line at the end.

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

Thresholds moved threefold in both directions. One config with absolute
numbers would have meant something completely different on those two machines —
which is the entire argument for this command.

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
