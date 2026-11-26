# The Marker Report — what to read and how to read it

`echolot analyze` writes two files into `.echolot/out/` next to the config:
`report.json` for tools and `report.md` for humans, one set of data. It prints
`report.md` only to a terminal; anywhere else — a pipe, an agent — it prints
the overview `echolot report` prints, and `--full` the whole report. A relative
`-o` is taken from the config's directory. A config with several scenarios
keeps a directory per scenario, `.echolot/out/<scenario>/`, and the report's
`scenario` says which. `--set detector.param=value` overrides one threshold
for one run and `--defaults` runs every detector on its built-in numbers; both
leave a mark (`config`, `params_source`), so such a report cannot be taken for
the project's.

This page is how to read it. The rest is a topic each, listed at the end; one
detector is `echolot explain <id>`.

## Read it in this order

1. **The overview**, `echolot report`: the window, the ⚠️ warnings above the
   findings, a line per detector, the commands for the rest.
2. **The warnings, before any row.** Each is a check below; one that says the
   window is not the scenario makes every number after it about something else.
3. **The rows the overview points at**: `echolot report -d <id>` for each
   detector that fired.
4. **Where the window went**: `echolot report --window` — the anchors, the
   main thread's time by state, how much of it the findings cover, the device.
5. **Your own markers**: `echolot report --markers`.

The json is the contract; it is not the thing to read whole. On a real hunt
the agent cut it up sixteen times with jq and python one-liners. The views are
those one-liners, done once:

```bash
echolot report                                 # what fired: one line per detector
echolot report --detector monitor_contention   # its rows by median × share of runs, 5 of them
echolot report -d main_thread_block --top 12   # more; --wide keeps the evidence whole
echolot report --window                        # anchors, the main thread, the device
echolot report --markers                       # your own names, measured
echolot report -d repeated_work --json         # the same selection as json — `places` included
echolot report path/to/report.json --window    # an older report, a round's own copy
```

Without a path it reads `.echolot/out/report.json` next to the config, or
`.echolot/out/<scenario>/report.json` for the scenario `--scenario` names,
the first one's without it.

## The verdict

`verdict.level` sets the window against the scenario's `budget_ms`: `fine`
(within it), `worth_a_look` (up to a quarter over), `needs_attention` (more),
`no_limit` (no budget), `not_checked` (the window is not the scenario's).
A detector's `attention`: `needs_attention` when one row of main-thread time
holds 15% of the window and 150 ms in every run (an `anr` row always),
`worth_a_look` for any other row, `move_marker` when its only rows are
`repeated_work`'s near misses, `fine` when it looked and found nothing,
`not_checked` when it did not look. `plain` says the same in words for a
person; no model writes it, and one report gives the same words every time.

## A detector says one of four things

- **Rows** — a finding. They come ranked, in one order everywhere: one trace
  by `self_ms` (`total_ms` where there is no self time); merged, by that median
  times the share of repeats in `runs`, so a 200 ms spike in `1/5` comes after
  60 ms in `5/5`.
- **Silent, `checked: true`** — that ground was checked and is clean. Do not
  go there.
- **`checked: false`** — it could not look: the trace lacks what it reads, and
  `reason` says what. Its empty `rows` are not silence and not good news;
  record again with what it reads.
- **`error` set** — it failed while the rest ran. Report the error; never read
  its empty rows as an answer. Not run at all is `summary.absent_ids`: the
  config turned it off.

## Columns

| column | meaning |
|---|---|
| `location` | the slice or thread name — what you hook onto |
| `runs` | merged: in how many repeats the row was found, of how many — `3/5` |
| `count` | how many times it occurred inside the window |
| `self_ms`, `total_ms` | without and with children: a large total over a small self time waits on its children, so dig there |
| `max_ms` | the longest single occurrence |
| `detail` | the evidence: thread, state, the lock's owner, what the samples named |
| `code`, `places` | where it is in the checkout: open `places[].file` at `line`, do not grep |
| `spread` | each run's value behind a median; `resolves_ms`, the least move a `compare` can call real |

Never add `total_ms` across rows: they nest. `self_ms` adds up within one
detector and never across two: a disk wait inside a slice is in both. How
much of the window the findings cover is `window.main_thread.in_rows_pct`. A
row with `threads` above one is a pool's time together, never the window's.
A marker with `unfinished: true` never closed, and its number, printed `≥`,
is a floor: say "at least".

## Before drawing a conclusion

- An anchor with `matches == 0`: the window ran to the edge of the trace and
  no number is about the scenario. Fix `scenario.start` or `scenario.end`
  from the names `echolot probe` shows; do not hunt.
- `window.end_anchor.unfinished`: the scenario never reached its end — a
  finding of its own.
- `window.opened_inside` with `material: true`: the window opened inside a
  stall whose cause is before it; move `scenario.start` earlier.
- `window.process_alternatives`: the mask matched several processes; narrow
  `project.process`.
- `drift.drifts: true`: the runs drift from the first to the last, and a
  median is no one state of the device.
- `environment.cpu` null, `environment.thermal.throttled`, or
  `environment.compile.filter` `verify` or `run-from-apk`: say so before
  calling a number slow.
- Another `toolchain.trace_processor`, or a detector's `sha`, between two
  reports: they read the trace differently.

## An empty report

No detector fired — two possibilities, and telling them apart is mandatory:

1. **The run is clean.** The window is plausible, the anchors matched
   (`matches > 0`).
2. **The config missed.** Check `window`: the duration is close to the whole
   trace, the anchors did not match, the process is the wrong one.

The second case is more common than the first.

## The rest, a topic each

Each is a file beside this one, `references/report-<topic>.md`, and the same
text from the command:

| command | what it holds |
|---|---|
| `echolot explain <id>` | one detector: what it reads, its thresholds here, its columns, what to watch for in its rows |
| `echolot guide report-schema` | `report.json` key by key: the repeats and those left out, `verdict` and `plain`, `identity`, `config`, `scenario`, `params_source`; which topic has every other key |
| `echolot guide report-window` | `window.main_thread`, `in_rows_pct`, `window.idle`, `crowding`, `frames`, `drawn`, `startup` |
| `echolot guide report-environment` | the device: clock, temperature, memory, compile state, callstack sampling, composable names |
| `echolot guide report-markers` | the markers table: `unfinished`, `absent`, `parents`, `mixed`, `unbuilt`, `removed` |
| `echolot guide report-checks` | every check above and the rest, each with what to do: `defined_by`, `recording`, `toolchain`, a merged report's misses |
| `echolot guide report-columns` | every column, the ranking, `code` and `places`, `spread`, a pool's rows |
| `echolot guide report-detectors` | every detector in a table, and what matters about each |
| `echolot guide report-compare` | `echolot compare`: `change`, `holds`, `shift`, `markers`, `names`, every warning |
