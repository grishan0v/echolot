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

## First time

```bash
cd ~/StudioProjects/my-app
echolot init
```

That installs the `.claude/` layer — the skill, the `perf-hunter` agent, the
commands — checks that this machine computes correctly, and ends with the next
step. Then open Claude Code in the project and type `/echolot`. That is the
one door: it asks the tool where the project stands and takes the next step
itself — the first time, that is building `echolot.yml` from the repository
and a probe trace (four questions to you along the way); every time after,
finding the cause of the regression you describe.

```
/echolot                              # reads the state, does what is next
/echolot why is cold start slow       # hunt, with that as the question
/echolot init · setup · hunt · reflect · doctor   # that thing, explicitly
```

## Coming back

```bash
echolot
```

Where things stand — layer, config, traces, last report, last `doctor` — and
one line saying what to do next; usually `/echolot`. After updating the
package, run `echolot init` again: it brings the layer up to date and leaves
the files you edited alone.

Without an agent, or in CI:

```bash
echolot collect -c echolot.yml -n 5                              # 5 repeats of the scenario
echolot analyze .echolot/traces/*.perfetto-trace -c echolot.yml  # the report
echolot doctor -q                                                # exit 0/1: does this environment compute correctly?
```

The output is `.echolot/out/report.md` for humans and `report.json` for the
agent. `doctor` builds a synthetic trace with problems planted in advance and
checks the answers against them — no device, about a second — good both as a
CI gate and as the agent's first move.

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

Two are yours to remember — `init` and `echolot` with nothing after it. The
rest split by who calls them.

**You**

```
echolot     where this project stands, and the next step
init        install or update the .claude/ layer; checks the environment
analyze     run the detectors, build a Marker Report — CI, or traces by hand
collect     capture N traces of one scenario: launch | command | gradle
doctor      environment + self-check on a synthetic trace + is the layer current; exit 0/1, -q for three lines
```

**The agent**, behind `/echolot` — you do not call
these:

```
probe       processes, threads by CPU, scenario anchor candidates
names       slice name inventory and detector mask coverage
domains     slice-to-code map and instrumentation coverage
calibrate   thresholds derived from known-healthy runs
explain     list the detectors and their parameters
```

**Improving the tool**

```
reflect     the same kind of report over an agent session — how the tool was used, where it got in the way
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

The detectors were validated on a synthetic trace (46 checks in `doctor`) and
on live traces from Android 14 (emulator) and Android 13 (Galaxy A51). The
naming masks for GC, locks and binder were narrowed against those real traces,
and every narrowing is pinned by a check.

**Not done: CI mode.** `scenario.budget_ms` is declared in the config but not
read by the code. What is needed is `analyze` with an exit code — either
against the budget or on "any detector fired", which after `calibrate` amounts
to a comparison against the baseline.

A failed detector never fails the run — the error goes to stderr and into
`report.json`.
