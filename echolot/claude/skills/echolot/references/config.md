# echolot.yml — the contract between the engine and the project

It lives in the Android project root and is committed. Read it like
`gradle.properties`: one tool per machine, the binding to a project inside that
project's repository.

Machine-local things go into `local.yml` next to it: `runner.device`, the
serial of the phone on this desk, and `toolchain.tp_binary`, a path to your
own `trace_processor_shell`. It belongs in `.gitignore`, and `echolot init`
adds it there when the project is the root of a git checkout. The merge is
recursive and local wins, key by key inside a section — but a list is a value
like any other and is replaced whole: a `runner.gradle_args` in local.yml
drops the committed arguments rather than adding to them. When local.yml is
applied, the CLI says so on stderr.

## What the code reads today

```yaml
project:
  package: com.example.app
  process: com.example.app     # GLOB over process.name
  source_root: app/src/main/kotlin
  mapping: app/build/outputs/mapping/benchmark/mapping.txt   # a minified build's R8 map

scenario:
  name: coldStart
  start:
    name: "bindApplication"
    _source: derived           # derived | confirmed_by_user | default
    _evidence: "probe: top slices, 245 ms on main"
  end:
    name: "Choreographer#doFrame*"
  budget_ms: 2500              # not read by the code yet, see below

runner:
  mode: launch                 # launch | command | gradle
  iterations: 5
  duration_ms: 12000
  reset_policy: force-stop     # force-stop (cold) | none (nothing between repeats)
  environment: true            # record CPU clock, thermal, memory
  sampling: false              # callstack samples, in Hz; true is 100

detectors:
  main_thread_block:
    min_slice_ms: 26.6

instrumentation:
  allowed: ["app/src/main", "feature/*/src/main"]
  temp_prefix: AGENTTMP_
```

### `project.process`

A GLOB, not an exact name. An app usually has several processes
(`:pushservice`, `:webview`), and `com.example.app*` catches them all. The CLI
takes the largest by slice count and **says so** — on stderr and in
`report.json` as `process_alternatives`. If the wrong process is being
analysed, narrow the mask.

### `project.mapping`

The R8 or ProGuard `mapping.txt` of the build that was recorded, for a
minified build. Its sampled frames come back as `a.b.c`, and `analyze` hands
this file to trace_processor, which names them back in every report the
build gives. Optional; without it, names stay as recorded. A relative path is
taken from the config's directory, and R8 writes the file to
`build/outputs/mapping/<variant>/mapping.txt`.

It needs `project.package`, the package as installed on the device — for a
benchmark build often the release's with a suffix — because the mapping
names back that package's frames. The mapping has to be **this** build's: one
from another build renames the frames it happens to match, wrongly, and leaves
the rest. The report's header warns when a tenth or more of the app's sampled
methods read as minified, with a mapping and without one; fewer than that is
code that arrived minified, as some SDKs ship, and no mapping of this build
names it.

### `scenario.start` / `scenario.end`

A GLOB over the slice name. Start is the first occurrence. End is where the
**first** anchor starting after that ends.

A thread's section or an async one — `Trace.beginAsyncSection`, which is what
an app's own markers usually are, and what a macrobenchmark's end marker
almost always is: a span that opens on one thread and closes wherever the
screen was first drawn. Such a section belongs to no thread; `probe` and
`names` list it under the thread name `(async)`, the anchors match it, and
the detectors never see it.

Both are optional: without them the window is the whole trace. For a trace from
a macrobenchmark that is fine — it has already cut out the measured block.

Names like `Choreographer#doFrame 55112` carry a vsync number that changes from
run to run. Such an anchor needs a wildcard.

**An anchor that did not match makes the report lie silently**, which is why
the CLI puts `matches` into `window`. Check it before drawing conclusions.

### `scenario.budget_ms`

Declared and **not read by the code**, deliberately: there is no performance
gate and none is planned. Do not build logic on it and do not expect a run to
fail when it is exceeded.

The question a budget answers — "did it get slower" — is already answered by
the benchmark that produced the trace, and thresholds evaluated on a shared CI
runner fire on properties of the runner. What echolot contributes is the other
question, *where* the time went, so in CI it belongs after the gate rather than
in it: `doctor -q` as a precondition, then `analyze`, with `report.json` kept
as a build artefact for whoever asks later.

Keeping the field is still worthwhile: it records what the team considers
acceptable, and it survives a change of device better than memory does.

### `runner`

`mode: launch` drives the scenario itself: force-stop, record, `am start -W`.
`mode: command` lets something else drive it (adb input, uiautomator, maestro,
your own script) while the trace records around it. `mode: gradle` runs a
macrobenchmark task and gathers the traces it wrote.

`reset_policy` is `force-stop` or `none`. `pm clear` is deliberately
unsupported: wiping data changes the scenario rather than repeating it.

`environment: true` records what the device was doing to the app while the
scenario ran — CPU frequency, thermal throttling, free memory, and why a
thread went into uninterruptible sleep. That last one carries two things: the
disk flag, which `io_wait` reads, and the kernel function, which comes back
empty on a production kernel and is named only on a userdebug one. It reaches
the report as `environment`, and `compare` reads it to tell a slower machine
from a slower app. Turning it off is for a device whose buffer overflows; the
report then says the device state was not recorded, which is a different
answer from "it held steady" — and `io_wait` goes silent, having no disk flag
to read.

`sampling` adds a callstack sampler to the recording: a number is the rate in
Hz, `true` is 100. It is off by default because it slows the app — on a cold
start of about 1.4 s, by some 190 ms at 100 Hz — so a sampled round compares
only with another sampled at the same rate, and `compare` warns when two are
not. The report's `environment.sampling` says whether a sampler ran and how
many of this process's samples came with a stack, and warns about the three
ways it comes back empty. Do not turn it on for one round of a comparison.

Every key the runner reads, and where it applies:

| key | mode | default |
|---|---|---|
| `mode` | every | `launch` |
| `iterations` | launch, command | 5; `collect -n` overrides it |
| `duration_ms` | launch, command | 12000 — the recording, not the scenario |
| `reset_policy` | launch, command | `force-stop` |
| `environment` | launch, command | `true` |
| `sampling` | launch, command | off; `true` is 100 Hz |
| `atrace_categories` | launch, command | `am wm gfx view dalvik binder_driver res database` |
| `buffer_kb` | launch, command | 131072 |
| `device` | launch, command | the one device attached; `collect --device` overrides it. Belongs in local.yml |
| `activity` | launch | the launcher activity of `project.package`, asked of the device |
| `command` | command | none — the mode needs it |
| `gradle_task` | gradle | none — the mode needs it |
| `gradle_args` | gradle | none |
| `gradle` | gradle | `./gradlew` |
| `project_root` | gradle | the config's directory; a relative path is taken from there, as `-o` is |
| `timeout_s` | gradle | 3600 — the whole gradle run, build included |

In gradle mode the macrobenchmark makes the recording, so the keys that shape
one — `environment`, `atrace_categories`, `buffer_kb`, `duration_ms`,
`reset_policy`, `sampling` — do not apply, and `collect` says so when it finds
them set.

### `detectors`

Thresholds, per detector, and nothing else. Naming one leaves the rest
running on the numbers they shipped with — you do not have to list them all
to tune one.

To turn a detector off, say so:

```yaml
detectors:
  main_thread_block:
    min_slice_ms: 26.6
  frame_jank: false        # this device has no frame timeline
```

`false` is the only way out. Before 0.6.0 the section doubled as an allowlist,
so a config naming six detectors ran six — and on a real project four sat out
for weeks because a calibrated section had been tidied.

The values override the `@param` defaults in the `.sql` files, and each has to
be the kind its default is — a number for a threshold, a string for a mask.
A value of the wrong kind, or a parameter the detector does not have, stops
`analyze` with exit 2 before any trace is read. Besides numbers they include
name masks: a parameter with `name_glob` in its name masks the slice name
(`name_glob_alt` too), one with `skip_glob` is an exclusion. They live in the
config because ART names things differently across Android versions, and
adapting to a device must not require editing a query.

`repeated_work.marker_prefix` is a parameter of that detector's own, and not
the same key as `instrumentation.temp_prefix`: it decides which names may come
back as a near miss, and it does not follow the other. A project that changes
its prefix changes both.

Thresholds are not picked by hand: `echolot calibrate` derives them from
healthy runs and prints a ready section with the reasoning attached.

### `instrumentation`

Where the agent may write temporary markers, and what to prefix them with.
`mark` reads both. A candidate outside `allowed` is still listed but not
applied, and its row says to mark the nearest allowed caller instead — which
caller that is, `mark` does not say. Each entry is compared path segment by
path segment, globs included: `feature/*/src/main` covers the main sources of
every module directly under `feature/`, the form `scan` writes. `--remove` does
not go by the prefix: it deletes the lines `--apply` wrote, recognised by the
`// echolot:mark` tag and their exact shape, and lists any other line carrying
the tag, with its file and line, for you to clean by hand. `compare` reads the
prefix too, to tell rows that appeared because markers were added between the
rounds from rows that appeared because the app did something new.

### Provenance

`_source` and `_evidence` give three things: a human sees what to double-check,
an agent knows that `confirmed_by_user` is untouchable, and when debugging you
can see where a piece of nonsense came from.

Untouchable holds inside a hunt too. A confirmed value that looks wrong — an
anchor that matched nothing in any run — is a question for the person, with
the matches and the candidates `echolot probe` lists; the loop stops there
instead of changing it. An investigation records the confirmed values when it
opens, and `analyze` inside it says when one no longer holds.

**The rule:** every field is justified by a finding. A slice name only if it
was found in the code or in the trace, with a `file:line` or a table row.
Nothing found — write `null` and say so out loud, do not invent something
plausible.

## What the agent reads, and the code reads in two places

```yaml
domains:                        # the slice-to-code map
  - slice: "collection_mapping"
    module: ":feature:collection"
    hint: "CollectionMapper.kt — entity→domain"

loop:
  max_rounds: 3
  on_exhausted: report

instrumentation:
  cleanup: always               # the agent's; no code reads it
```

`domains` is the central abstraction: it turns a marker into a hypothesis
without scanning the repository blindly, and blind scanning is the main context
eater. `echolot domains` pre-fills it from the sources — literals inside
tracing calls, and names kept in a `const val` and passed to the project's
own wrapper (`AppTraces.start(LOAD)`), which is how most apps that
name their markers write them. A hint ending in `via X` says the literal is
not on that line: `X` is.

`analyze` reads `domains[].slice`: every name listed there, taken as a GLOB,
is measured in the Markers table of each report whatever the detectors say,
and one the window never held is listed as absent. `module` and `hint` are
for the reader alone.

`loop.max_rounds` is the one number a human sets to bound a hunt. Stopping is
not left to the agent's judgement: it has no goal of its own to economise.
`reflect` reads it too, to say whether a hunt went past it.

`instrumentation.cleanup` sits in a section the code reads and is the one key
of it the code does not. It is the agent's: `perf-hunter` takes out every
`AGENTTMP_` marker before it returns, on success and on running out of rounds
alike, and SKILL.md has the ones a previous investigation left behind taken
out before the next one starts. `always` is what the two of them do whatever
the key says, so another value changes no run.

## Read by nobody yet

```yaml
threads:
  own: ["DefaultDispatch*", "arch_disk_io_*"]
  ignore: ["HeapTaskDaemon", "Jit thread pool"]
```

Which threads carry the application's own work, and which are the runtime's
housekeeping. Nothing consumes it today — not the CLI, not the agent — so it
records an intention and changes no run. Do not read a finding out of it, and
do not tell anyone a thread was ignored because it is listed here.

It is worth writing down anyway: `uninstrumented_cpu` reports any thread over
its bar, and separating the housekeeping from the application's own work is
currently left to whoever reads the report.

Masks rather than names, because `comm` is truncated to fifteen characters:
`DefaultDispatcher-worker-1` arrives as `DefaultDispatch` and the whole pool
comes under that single name, while digits inside the cut survive and
`arch_disk_io_*` still matches four distinct threads. See the naming
reference: `echolot guide naming`.
