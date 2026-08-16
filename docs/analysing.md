# From a trace to a place in the code

Three commands sit between a trace you know nothing about and a finding you can
act on: `probe` tells you what is inside, `names` tells you what the detectors
will see, `domains` maps a finding back to a file.

## `probe` — what is inside at all

```bash
echolot probe trace.perfetto-trace --process 'com.example.*'
```

Processes, threads and the longest slices. This is what fills in the config
during setup: the anchor candidates it prints come from a real trace rather
than from someone's memory.

The thread table is sorted by **CPU time, not slice count**. A thread with zero
slices and hundreds of milliseconds of Running is exactly the blind spot you
are looking for; sorted by slice count it would sit at the bottom.

## `names` — how ART names things here

Four of the six detectors are structural: duration, thread, scheduler state.
They do not care what anything is called. But `gc_pressure`,
`monitor_contention` and `binder_txn` search by **name**, and the names are
invented by ART, differing across Android versions and vendors. You cannot
guess them — you can look:

```bash
echolot names trace.perfetto-trace --process 'com.example.*'
echolot names trace.perfetto-trace          # next to echolot.yml: its process
```

Without `--process` and without a config the process with the most slices is
taken — on a real device that is `surfaceflinger`, not the app, and the
command says so on stderr. With `echolot.yml` in the working directory
`project.process` is the default, as it is for `analyze`.

The command collapses names that differ only by numbers (`owner tid: 1234` and
`owner tid: 5678` are one phenomenon — without this, a real trace yields
thousands of rows), sorts them into sections, and shows which detector masks
land on them.

The section that matters most is **"Missed by the masks"**: everything that
looks like GC, locks or binder yet no detector will see. That is the list to
act on.

Two subtleties in the output. Something excluded on purpose via `skip_glob` is
marked as excluded rather than missed — it is a decision, not a gap. And the
mask column speaks only about detectors that search by name; a dash next to
`AppStart` does not mean nobody will find it.

### Masks live in the config

```yaml
detectors:
  gc_pressure:
    name_glob: "*GC"
```

The convention: `*name_glob*` masks the slice name, `*thread_glob*` the thread
name, `*skip_glob*` is an exclusion. `names` reads them itself, so a new
detector with masks appears in the report without any registration.

They live in the config rather than in SQL because adapting to a device must
not require editing a query. What ART actually calls things on Android 14 is
written down in `echolot/claude/skills/echolot/references/naming.md`.

## `domains` — the slice-to-code map

```bash
echolot domains --root .
```

`domains` is the central abstraction of the config: it turns a name from the
report into a hypothesis without scanning the repository blindly, and blind
scanning is the main context eater.

It is assembled mechanically, because a slice name is a string literal that
survives minification and is found by exact search:

```yaml
domains:
  - slice: "collection_mapping"
    module: ":feature:collection"
    hint: "Mapper.kt:5 — fun mapEntities"
```

The module comes from the nearest ancestor holding a build script. `hint` is
for humans; the engine never reads it, so fix the wording when it is imprecise.

Two precision rules worth knowing:

- a bare `trace("…")` counts only in files that import `androidx.tracing`,
  otherwise every logging function with that name lands in the map;
- `build` and `generated` are not scanned — generated code is no place for
  hypotheses.

Calls with a non-literal name (`Trace.beginSection(tag)`) cannot reach the map
at all. They are counted separately and mentioned in the header: they are
visible in the trace, and staying quiet about them would pass a gap off as its
absence.

### When there is no instrumentation

Then the output is not an empty section but a coverage report and the modules
with the most code and none of it instrumented:

```
# Instrumentation: 0 tracing calls across 43654 lines of source.
#
# No instrumentation. There is nothing to attach findings to…
#
#   :app                           37901 lines, 583 files
#   :design-system                  4235 lines, 48 files
```

That is an honest answer to "how much do we need to instrument before this
works": the modules with the most code and none of it, by name.

`uninstrumented_cpu` works without any instrumentation anyway — it will show
which of those modules actually burns CPU. And where the first markers go —
the entry points, from the manifest and the SDK, with a source on every row
— is `echolot mark`, in [mark.md](mark.md).
