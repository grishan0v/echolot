# echolot

A deterministic layer between a Perfetto trace and an AI agent.

The agent **never looks at the trace**. A trace is gigabytes, and a model
drowns in them: it burns tokens, wanders into side quests, and reaches
different conclusions from one run to the next. Between the trace and the model
sits a CLI that compresses the trace with pre-written SQL down to a table of
twenty rows.

```
81 MB trace, 475k slices   →   14 KB report.json   in 5 seconds
```

The agent reasons over facts instead of digging for them.

Side effect: the tool is useful with no AI involved at all — it is a set of
ready-made detectors you can hang in CI and catch regressions for zero tokens.

## Install

```bash
pipx install git+https://github.com/grishan0v/echolot.git
```

`trace_processor_shell` does not need installing — the `perfetto` package
downloads and caches the binary on first use.

## Start

```bash
echolot doctor                      # does this environment compute correctly?
cd ~/StudioProjects/my-app
echolot init                        # install the .claude/ layer
cp echolot.yml.example echolot.yml  # or run /echolot-setup and let the agent fill it
echolot collect -c echolot.yml -n 5 # capture 5 repeats of the scenario
echolot analyze .echolot/traces/*.perfetto-trace -c echolot.yml
```

The output is `.echolot/out/report.md` for humans and `report.json` for the
agent.

Start with `echolot doctor`: it builds a synthetic trace with problems planted
in advance and checks the answers against them. Exit code 0/1, no device
needed, one second — good both as a CI gate and as the agent's first move.

## What it looks for

| detector | what it catches |
|---|---|
| `main_thread_block` | where the main thread spent its time (by self time) |
| `gc_pressure` | frequent or expensive GC, and waits on allocation |
| `monitor_contention` | monitor contention, with the owner's tid as evidence |
| `binder_txn` | long synchronous IPC, and death by a thousand cuts |
| `runnable_starvation` | thread ready but preempted on CPU |
| `uninstrumented_cpu` | **threads burning CPU with no instrumentation** |

The last one is the only detector that finds a problem inside uninstrumented
code. It does not guess; it presents a fact: "thread
`DefaultDispatcher-worker-2` was Running for 340 ms, zero slices". That is
where to add `trace{}` and re-record.

Each detector is one self-contained `.sql` file with its metadata in the
header. Drop a file into `echolot/sql/detectors/` and it is picked up — there
is no registration in code.

## The commands

```
doctor      environment + self-check on a synthetic trace, exit 0/1
init        install the .claude/ layer into an Android project
collect     capture N traces of one scenario: launch | command | gradle
domains     slice-to-code map and instrumentation coverage
probe       processes, threads by CPU, scenario anchor candidates
names       slice name inventory and detector mask coverage
calibrate   thresholds derived from known-healthy runs
analyze     run the detectors, build a Marker Report
explain     list the detectors and their parameters
reflect     the same kind of report over an agent session — for improving the tool
```

## Where things live

```
android-project/
├── echolot.yml       ← the project half, committed
├── local.yml         ← device serials, binary path; in .gitignore
└── .echolot/         ← traces, reports, the run log, reflect reports; in .gitignore
```

Read it like `gradle.properties` and `local.properties`: one tool per machine,
the binding to a project inside that project's repository.

## Documentation

| document | about |
|---|---|
| [docs/collecting.md](docs/collecting.md) | `collect`, the three modes, merging repeats |
| [docs/analysing.md](docs/analysing.md) | `probe`, `names`, `domains` — from a trace to a place in the code |
| [docs/detectors.md](docs/detectors.md) | writing your own, the context views, self time versus total |
| [docs/calibrate.md](docs/calibrate.md) | thresholds from healthy runs, why rank beats percentile |
| [docs/determinism.md](docs/determinism.md) | the pinned trace_processor, `doctor`, the self-check |
| [docs/agent-layer.md](docs/agent-layer.md) | the `.claude/` layer and why the loop lives in a subagent |
| [docs/reflect.md](docs/reflect.md) | `reflect` — the report over the agent's session, for improving the tool |

The agent-facing reference material ships inside the package, under
`echolot/claude/skills/echolot/references/` — the report schema, the config
schema, how ART names things, and how to capture a trace by hand.

## Status

v0. Everything planned for it is in place except CI mode.

The detectors were validated on a synthetic trace (35 checks in `doctor`) and
on live traces from Android 14 (emulator) and Android 13 (Galaxy A51). The
naming masks for GC, locks and binder were narrowed against those real traces,
and every narrowing is pinned by a check.

**Not done: CI mode.** `scenario.budget_ms` is declared in the config but not
read by the code. What is needed is `analyze` with an exit code — either
against the budget or on "any detector fired", which after `calibrate` amounts
to a comparison against the baseline.

A failed detector never fails the run — the error goes to stderr and into
`report.json`.
