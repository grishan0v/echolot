# echolot.yml — the contract between the engine and the project

It lives in the Android project root and is committed. Read it like
`gradle.properties`: one tool per machine, the binding to a project inside that
project's repository.

Machine-local things (device serials, a path to your own
`trace_processor_shell`) go into `local.yml` next to it, which sits in
`.gitignore`. The merge is recursive and local wins; when it is applied, the
CLI says so on stderr.

## What the code reads today

```yaml
project:
  package: com.example.app
  process: com.example.app     # GLOB over process.name
  source_root: app/src/main/kotlin

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
  reset_policy: force-stop     # force-stop (cold) | none (warm)

detectors:
  main_thread_block:
    min_slice_ms: 26.6
```

### `project.process`

A GLOB, not an exact name. An app usually has several processes
(`:pushservice`, `:webview`), and `com.example.app*` catches them all. The CLI
takes the largest by slice count and **says so** — on stderr and in
`report.json` as `process_alternatives`. If the wrong process is being
analysed, narrow the mask.

### `scenario.start` / `scenario.end`

A GLOB over the slice name. Start is the first occurrence. End is where the
**first** anchor starting after that ends.

Both are optional: without them the window is the whole trace. For a trace from
a macrobenchmark that is fine — it has already cut out the measured block.

Names like `Choreographer#doFrame 55112` carry a vsync number that changes from
run to run. Such an anchor needs a wildcard.

**An anchor that did not match makes the report lie silently**, which is why
the CLI puts `matches` into `window`. Check it before drawing conclusions.

### `scenario.budget_ms`

Declared but **not read by the code**: it needs CI mode — `analyze` with an
exit code driven by the budget — which is deferred to a later version. Do not
build logic on it and do not expect a run to fail when it is exceeded.

Keeping the field is still worthwhile: it records what the team considers
acceptable, and it survives a change of device better than memory does.

### `runner`

`mode: launch` drives the scenario itself: force-stop, record, `am start -W`.
`mode: command` lets something else drive it (adb input, uiautomator, maestro,
your own script) while the trace records around it. `mode: gradle` runs a
macrobenchmark task and gathers the traces it wrote.

`reset_policy` is `force-stop` or `none`. `pm clear` is deliberately
unsupported: wiping data changes the scenario rather than repeating it.

### `detectors`

Listing keys means **enable only these**. To adjust one threshold without
disabling the rest, list them all.

The values override the `@param` defaults in the `.sql` files. Besides numbers
they include name masks: `*name_glob*` over the slice name, `*thread_glob*`
over the thread, `*skip_glob*` for exclusions. They live in the config because
ART names things differently across Android versions, and adapting to a device
must not require editing a query.

Thresholds are not picked by hand: `echolot calibrate` derives them from
healthy runs and prints a ready section with the reasoning attached.

### Provenance

`_source` and `_evidence` give three things: a human sees what to double-check,
an agent knows that `confirmed_by_user` is untouchable, and when debugging you
can see where a piece of nonsense came from.

**The rule:** every field is justified by a finding. A slice name only if it
was found in the code or in the trace, with a `file:line` or a table row.
Nothing found — write `null` and say so out loud, do not invent something
plausible.

## The contract for later (not read by the code yet)

```yaml
domains:                        # the slice-to-code map
  - slice: "collection_mapping"
    module: ":feature:collection"
    hint: "CollectionMapper.kt — entity→domain"

threads:
  own: ["DefaultDispatch*", "OkHttp*"]   # comm is truncated to 15 characters
  ignore: ["HeapTaskDaemon", "Jit thread pool"]

loop:
  max_rounds: 3
  on_exhausted: report

instrumentation:
  allowed: ["app/src/main", "feature/*/src/main"]
  temp_prefix: AGENTTMP_
  cleanup: always
```

`domains` is the central abstraction: it turns a marker into a hypothesis
without scanning the repository blindly, and blind scanning is the main context
eater. `echolot domains` pre-fills it from the sources.
