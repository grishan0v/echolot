<p align="center">
<img width="429" height="128" alt="echolot-lockup-inverse-2x" src="https://github.com/user-attachments/assets/1cea634c-f0cd-4b32-a32f-c221e2be8227" />
</p>
<p align="center">
  <b>CLI that helps your agent to find performance issues in Android apps.</b>
</p>

<p align="center">
  <a href="https://github.com/grishan0v/echolot/actions/workflows/checks.yml"><img alt="checks" src="https://github.com/grishan0v/echolot/actions/workflows/checks.yml/badge.svg?branch=main"></a>
  <a href="https://github.com/grishan0v/echolot/actions/workflows/codeql.yml"><img alt="codeql" src="https://github.com/grishan0v/echolot/actions/workflows/codeql.yml/badge.svg?branch=main"></a>
  <a href="https://pypi.org/project/echolot/"><img alt="PyPI" src="https://img.shields.io/pypi/v/echolot.svg"></a>
  <a href="https://pypi.org/project/echolot/"><img alt="Python versions" src="https://img.shields.io/badge/3.10--3.14-blue?logo=python&logoColor=white"></a>
  <a href="https://github.com/grishan0v/echolot/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache%202.0-blue.svg"></a>
  <a href="#status"><img alt="Status" src="https://img.shields.io/badge/status-v0-orange.svg"></a>
</p>

---

**Contents** · [What it is](#what-it-is) · [Requirements](#requirements) · [Quick start](#quick-start) · [What you get](#what-you-get) · [What changed](#what-changed) · [Commands](#commands) · [Detectors](#detectors) · [How it works](#how-it-works) · [Project layout](#project-layout) · [Documentation](#documentation) · [Status](#status)

---

## What it is

A Perfetto trace of one cold start holds around half a million slices in eighty
megabytes. Nobody reads that, and an AI agent pointed at the raw file produces
confident guesses instead of answers.

echolot sits in between. It runs twelve SQL detectors over the trace and returns
about twenty rows: where the time went, how much of it, and the evidence behind
each claim. Same trace in, same report out — the `trace_processor` version is
pinned, and the binary is checked against its SHA-256 when it is downloaded.

> [!TIP]
> The intended way to use it is through Claude Code: you describe the
> regression in plain words, the agent collects traces, reads the report and
> walks down to the code. The command line works on its own too — see
> [without an agent](#without-an-agent).

**Using Cursor, Codex or something else?** `echolot init` points them at the
tool, and `echolot guide` tells any agent how to work with it. The loop runs
in your main context rather than a subagent, so keep the passes short — the
guide says where that matters.

`echolot reflect` works from any of them: with no transcript to read it builds
the report from the tool's own run log, and names every check it could not
make rather than reporting silence as a clean bill.

## Requirements

| | |
|---|---|
| **Python** | 3.10 or newer |
| **`curl`** | on `PATH` — the one download in the next row goes through it |
| **`trace_processor`** *(fetched once)* | the first command that needs it — usually `echolot init`, at its environment check — downloads the build the `perfetto` package pins for your OS and CPU: 10–14 MB, into `~/.local/share/perfetto/prebuilts/`, checked against its SHA-256. It says so on stderr as it starts. Behind a proxy, export `HTTPS_PROXY`. Offline, copy the `trace_processor_shell-…` file from that directory on a machine with the same OS and CPU, under the same name. See [Determinism](https://github.com/grishan0v/echolot/blob/main/docs/determinism.md) |
| **`adb`** | on `PATH` — ships in the Android SDK platform-tools |
| **Device** | a phone or emulator with USB debugging on |
| **Agent** *(optional)* | [Claude Code](https://claude.com/claude-code) for the full workflow; Cursor, Codex and others via `echolot guide` |
| **Android 12+** *(for one detector)* | `frame_jank` reads SurfaceFlinger's frame timeline. Older devices do not have it, and the detector is then silent — which reads exactly like "no bad frames" |

Validated on Android 14 (emulator) and Android 13 (Galaxy A51).

## Quick start

### 1. Install

```bash
pipx install echolot
echolot --version
```

`--version` names what you got: echolot, the `trace_processor` it pins,
`perfetto` and Python. The binary itself arrives later, once — see
[Requirements](#requirements). Installed with `pip` into an environment whose
scripts are not on `PATH`, `python -m echolot` is the same command.

### 2. Set up your project

Run this once inside your Android project:

```bash
cd ~/my-app && echolot init
```

This installs the `.claude/` layer — a skill, the `perf-hunter` agent and three
commands — and checks that this machine computes traces correctly. When the
project directory is the root of a git checkout, `echolot init` also adds
`.echolot/` and `local.yml` to its `.gitignore`; otherwise it says it did not
and prints the two lines to add.

### 3. Open the agent and type one word

```
/echolot
```

That is the only entry point you need to remember. It asks the tool where the
project stands and takes the next step by itself:

- **first run** — builds `echolot.yml` from your repository and a probe trace,
  asking you four questions along the way;
- **every run after** — hunts down the regression you describe.

```
/echolot                          reads the state, does whatever is next
/echolot why is cold start slow   hunt, with that as the question
/echolot init | setup | hunt | reflect | doctor
```

Each word on the last line is also an `echolot` command, except `setup`:
building `echolot.yml` needs an agent, and there is no `echolot setup` in the
shell.

### Coming back later

```bash
echolot
```

That is `echolot status`. It prints where the project stands — the layer, the
config, the investigation, the traces, the last report and the last `doctor`,
plus a `collect` line while a run is going or after one stopped short — and
one line saying what to do next.

> [!IMPORTANT]
> After upgrading the package, run `echolot init` again. It brings the
> `.claude/` layer up to date and leaves files you edited alone. A teammate
> still on an older echolot is then told to upgrade: their `echolot` says
> so, and their `init` refuses rather than put the older files back.
> Releases up to 0.7.0 do not have that rule, so it holds once the whole
> team is on a later one.

### Without an agent

The same work, by hand or in CI:

```bash
echolot collect -c echolot.yml -n 5                              # 5 repeats of the scenario
echolot analyze .echolot/traces/*.perfetto-trace -c echolot.yml  # build the report
echolot compare before.json .echolot/out/report.json             # what changed since
echolot doctor -q                                                # is this environment sane? exit 0 yes, 1 no or not checked, 2 trace_processor not downloaded
```

Results land in `.echolot/out/` — `report.md` for you, `report.json` for the
agent.

## What you get

A **Marker Report**: a header saying what was measured and against which
config, a table of your own markers when there are any, then one section per
detector that fired. The silent ones are only named.

<details>
<summary><b>Example report</b> (click to expand)</summary>

```markdown
# Marker Report

Runs: **5**, numbers are medians across them
Traces: `coldStart_iter000`, `coldStart_iter001`, `coldStart_iter002`, `coldStart_iter003`, `coldStart_iter004`
Process: `com.example.app` (pid 12903)
Scenario window: **1184.37 ms** (from 1102.14 to 1291.52)
Main thread: 49% on a CPU · 8% waiting for a CPU · 10% blocked in the kernel · 33% sleeping
Covered by the findings below: **31%** of the main thread's window, each moment counted once
Device: clock **1481 MHz** (from 1204 to 1622 across repeats), peak 54 °C, 1536 MB free at the low point
Detectors fired: **5 of 12**
Config: `/home/you/my-app/echolot.yml` (sha 3f9a1c2b7d40) · thresholds: built-in defaults

## Markers
_the names `domains` lists and the `AGENTTMP_` ones; medians per run, self time with children subtracted_

| Marker | Runs | N | Self, ms | Total, ms | Max, ms | Threads |
|---|---|---|---|---|---|---|
| collection_load | 5/5 | 1 | 212.4 | 212.4 | 212.4 | (async) |
| collection_mapping | 5/5 | 1 | 96.0 | 118.3 | 118.3 | arch_disk_io_1 |

## Frames that missed their deadline
_the platform classifies every frame itself, and says whose fault it was. The only detector that answers "which frames stuttered" rather than "where did the total go" — and it needs no instrumentation in the app at all._

| Where | Runs | N | Total, ms | Max, ms | Evidence |
|---|---|---|---|---|---|
| App Deadline Missed | 5/5 | 14 | 412.0 | 70.1 | Self Jank · 14 of 300 frames · longest 86.2 ms |

<sub>detector `frame_jank`, params: {'min_frames': 3, 'min_overrun_ms': 4}</sub>

## Where the main thread spent its time
_measured as SELF time, children subtracted — otherwise one event lands in the report several times at different depths and the agent counts it twice_

| Where | Runs | N | Self, ms | Total, ms | Max, ms | Evidence |
|---|---|---|---|---|---|---|
| draw | 5/5 | 4 | 125.4 | 130.1 | 61.2 | com.example.app |
| TextLayout:initLayout | 5/5 | 61 | 88.0 | 88.0 | 4.1 | com.example.app |
| inflate | 5/5 | 12 | 47.3 | 162.4 | 21.7 | com.example.app |

<sub>detector `main_thread_block`, params: {'min_slice_ms': 16}</sub>

## Single occurrences far longer than the same work usually takes
_the pair to main_thread_block. That one asks where the main thread's time went in total; this one asks which single occurrence was out of line with its own history. A heavy tail hides from every sum._

| Where | Runs | N | Total, ms | Max, ms | Evidence |
|---|---|---|---|---|---|
| inflate | 2/5 | 1 | 86.2 | 86.2 | median 12.9 ms of 12 · worst 6.7× |

<sub>detector `main_thread_outlier`, params: {'factor': 4, 'min_abs_ms': 40, 'min_occurrences': 5}</sub>

## Monitor contention
_ART writes a "Lock contention on ..." slice with the owner's tid — ready-made evidence_

| Where | Runs | N | Total, ms | Max, ms | In the code | Evidence |
|---|---|---|---|---|---|---|
| com.example.app | 5/5 | 9 | 61.5 | 22.4 | owner at StoreRepository.kt:30 · blocked at StoreRepository.kt:66 | monitor contention with owner DefaultDispatcher-worker-3 (12931) at void com.example.app.data.StoreRepository.update(com.example.app.data.Item)(StoreRepository.kt:30) waiters=0 blocking from com.example.app.data.Item com.example.app.data.StoreRepository.find(long)(StoreRepository.kt:66) |

<sub>detector `monitor_contention`, params: {'min_block_ms': 8, 'max_total_ms': 50, 'name_glob': 'Lock contention on a monitor lock*', 'name_glob_alt': 'monitor contention with owner*'}</sub>

## Blind spots: threads burning CPU with no instrumentation
_the ONLY detector that finds a problem inside uninstrumented code. The agent does not guess — it is handed the fact "thread T ran for 340 ms, zero slices". That is exactly where adding trace{} pays off._

| Where | Runs | N | Total, ms | Instrumented, ms | Evidence |
|---|---|---|---|---|---|
| DefaultDispatch | 5/5 | 0 | 340.2 | 0.0 | 100.0% of CPU outside slices |

<sub>detector `uninstrumented_cpu`, params: {'min_running_ms': 50, 'max_covered_pct': 50}</sub>

**Silent:** anr, anr_risk, binder_txn, gc_pressure, io_wait, repeated_work, runnable_starvation

<sub>trace_processor v56.1</sub>
```

</details>

The report is written for two readers at once: `report.md` reads like a
findings list, `report.json` carries the same numbers in a shape the agent can
walk. An 81 MB trace with 475k slices comes out as a 14 KB `report.json` in
about five seconds.

## What changed

The report says where the time went in one set of traces. The question people
arrive with has a second half — *it was 3 s, now it is 7 s* — and that needs
two sets.

```bash
echolot compare                       # inside an investigation: previous round vs latest
echolot compare old.json new.json     # or name them
```

One table, sorted by how far each row moved. The top row is usually the answer.

| Where | Evidence | Detector | Before | After | Δ | N | Holds |
|---|---|---|---|---|---|---|---|
| SyncAdapterThre | — | uninstrumented_cpu | — | 1402.0 ±61 | **new** | — → 0 | — |
| TeamRepository.loadAll | com.example.app | main_thread_block | 12.1 ±2 | 883.4 ±40 | **+871.3 ×73.01** | 1 → 1 | yes, +831.3 … +911.3 |
| inflate | com.example.app | main_thread_block | 47.3 ±31 | 121.9 ±88 | +74.6 ×2.58 | 12 → 31 | no, -13.4 … +162.6 |

`N` separates "called more often" from "became slower inside" — two different
bugs in two different places. **Holds** is the column that decides whether a
row is worth acting on. It gives the range the move lies in, 95% sure, worked
out from every run after paired with every run before: `yes` when the whole
range is on one side of zero, `no` when it runs through zero. `no` means the
runs disagree among themselves by more than the row moved, and the honest next
step is another round of `collect` rather than a conclusion.

Reports built against different thresholds are compared with the reason printed
above the table — a row can cross a moved bar without anything in the app
changing. The same goes for the machine: a duration is the work done divided by
the speed the device was doing it at, so a clock that moved 10% or more
between the rounds, in either direction, or a kernel that throttled during one
of them, is named above the table too. Silence there means the device was
checked and held steady; a round recorded without the platform-state sources
says that instead.
See [Comparing](https://github.com/grishan0v/echolot/blob/main/docs/compare.md).

## Commands

Four groups share one CLI, and `echolot --help` says which is which. Its list
is generated from the same registration that defines the commands, so it
cannot drift from them; the tables below are written by hand and follow its
grouping.

Every verb after `/echolot` is an `echolot` command of the same name, doing
the same thing plus whatever loop needs an agent — except `setup`, which only
the agent has. One word, one meaning, both surfaces.

### Yours

| command | what it does |
|---|---|
| `echolot` | where this project stands, and the next step |
| `echolot init` | install or update the `.claude/` layer; .gitignore, and checks the environment |
| `echolot hunt "<what regressed>"` | open an investigation — see [below](#the-investigation) |
| `echolot doctor` | environment + self-check on a synthetic trace; exit 0 when every check passes, 1 when one fails or the self-check cannot run, 2 when trace_processor cannot be downloaded; `-q` for three lines |

### The pipeline — for CI, and for traces by hand

| command | what it does |
|---|---|
| `echolot collect` | capture N traces of one scenario — `launch`, `command` or `gradle` |
| `echolot analyze` | run the detectors, build a Marker Report |
| `echolot compare` | the difference between two reports — see [below](#what-changed) |

<details>
<summary><b>The agent's, behind <code>/echolot</code></b> — you do not call these</summary>

<br>

| command | what it does |
|---|---|
| `guide` | how to work with this tool, printed by the package — what an agent without the `.claude/` layer reads instead of it |
| `report` | views of the last report without opening the json: what fired, one detector's rows with the evidence kept short, the window, the markers — `--json` for any of them |
| `scan` | what the repository says about itself, read as text: the app module and its applicationId, the variants and which one to measure on, the macrobenchmark with its tests and the sections it measures, the gradle task that runs it, the devices attached — and an `echolot.yml` to start from |
| `anr` | an ANR report from the field — the lock chain, the few threads that were not idle, and where their frames are in this checkout. Crashlytics and Play Console exports, and the device's own `dumpsys dropbox` record |
| `probe` | processes, threads by CPU, scenario anchor candidates — the threads' sections and the process's async ones |
| `names` | slice name inventory and detector mask coverage |
| `domains` | slice-to-code map and instrumentation coverage — literals, and names kept in a `const val` and passed through the project's own wrapper |
| `mark` | the first temporary markers for a project with none, from the manifest and the SDK, or from an ANR report's own frames with `--from-anr` — `--apply` / `--remove`. `--pools` lists where a thread or pool is created with the JDK's default name instead |
| `calibrate` | thresholds derived from known-healthy runs |
| `explain` | list the detectors and their parameters |

</details>

<details>
<summary><b>Improving the tool</b></summary>

<br>

| command | what it does |
|---|---|
| `reflect` | the same kind of report, over an agent session — how the tool was used, where it got in the way. Full detail for Claude Code; from anywhere else, built from the run log and honest about what it could not see |

</details>

## The investigation

`.echolot/traces/` and `.echolot/out/report.json` mean "the latest set". An
investigation is the label that says which question that set was recorded for,
so that coming back a week later does not answer a question about scrolling
with cold-start traces.

```bash
echolot hunt "cold start was 3s, now 7s" --since "the tab redesign"
```

That opens one, moves the previous set of traces aside without deleting it,
and says whether the last investigation left temporary markers in your
sources. `echolot hunt` on its own says what is open.

```
echolot hunt --list          every investigation, newest first
echolot hunt --show 2        one of them in full — including where its traces went
echolot hunt --resume        carry on with the open one
echolot hunt --done "..."    record what it came to
```

Each one is numbered, and everything it produces is filed under it: every
round of traces by path, every report as a copy in
`.echolot/hunts/<n>/reports/`. So a question asked three weeks ago still knows
what was measured to answer it, and what each round concluded on the way.

Nothing moves to make this work — `collect` still writes to `.echolot/traces/`
and the latest report is still `.echolot/out/report.json`, so every example
above and any CI job keep working unchanged.

You rarely type any of it. `/echolot` reads the state and, when an
investigation has been sitting untouched with traces behind it, asks whether
to carry on or start something new — and never asks inside the hunting loop,
which re-records and re-instruments on purpose.

## Detectors

| detector | what it catches |
|---|---|
| `main_thread_block` | where the main thread spent its time, by self time |
| `gc_pressure` | frequent or expensive GC, and waits on allocation |
| `monitor_contention` | lock contention, with the owner's tid as evidence |
| `binder_txn` | long synchronous IPC, and death by a thousand cuts |
| `runnable_starvation` | thread ready to run but preempted on CPU |
| `uninstrumented_cpu` | **threads burning CPU with no instrumentation** |
| `frame_jank` | frames that missed their deadline, and whose fault it was |
| `anr_risk` | stretches where the main thread never got back to the message queue |
| `anr` | ANRs the system recorded during the trace, with its own reason |
| `main_thread_outlier` | one occurrence far longer than that work usually takes |
| `repeated_work` | the same work reached from more than one caller, costing about the same both times |
| `io_wait` | **threads the kernel parked waiting for a block device** |

Three of them find something where nobody wrote a `trace{}` call.

`uninstrumented_cpu` does not guess — it states a fact:

> thread `DefaultDispatch` was Running for 340 ms, zero slices

Which is exactly where to add `trace{}` and record again. The name is cut
at fifteen characters by Linux rather than by echolot: every worker of that
pool reaches the trace as `DefaultDispatch`, so write thread masks with the
truncation in mind.

`io_wait` needs none either, and it is the one that answers a question the
other two cannot even ask. A thread waiting for the disk burns no CPU, holds
no lock of yours and has no slice around it: there is nothing to profile and
nothing to instrument, so a cold start can spend hundreds of milliseconds
there with every other detector silent. The kernel is the only witness, and
it says so through `sched/sched_blocked_reason`.

It names the thread and the milliseconds, not the kernel function. Turning the
blocking address into a name needs `/proc/kallsyms`, which a production build
does not let anyone read — on an SM-A515F the function came back empty for all
6683 uninterruptible sleeps in the trace while the disk flag was set on 6486
of them. The name appears on a userdebug kernel and the row is worth acting on
without it.

`frame_jank` needs no instrumentation at all: SurfaceFlinger records every
frame's deadline and what it actually took, and says whose fault a miss was.
Android 12 and up — see [requirements](#requirements).

`main_thread_block` and `main_thread_outlier` are a pair, and reading one as
the other wastes a round. The first gates on the **sum** for a name and answers
"where did the time go". The second gates on a **single occurrence** against
the median for that same name and answers "which one was out of line". A name
can appear in both, saying different things — and they lead to different
places: expensive every time means the fix is in that work, usually fine and
once not means the cause is the state it hit that once.

> [!NOTE]
> Each detector is one self-contained `.sql` file with its metadata in the
> header. Drop a file into `echolot/sql/detectors/` and it runs — there is no
> registration step in code. Shipping it takes more than running: `doctor`'s
> self-check fails until the fixture plants a problem for it or says why it
> cannot, and the tests fail until the detector counts in this README are
> brought up to date. The checklist is in
> [docs/detectors.md](https://github.com/grishan0v/echolot/blob/main/docs/detectors.md).

## How it works

```mermaid
flowchart LR
    A["Android device"]
    B["trace<br/>81 MB · 475k slices"]
    C["12 SQL detectors<br/>pinned trace_processor"]
    D["report.md<br/>~20 rows"]
    E["report.json<br/>14 KB"]
    H["comparison<br/>what moved, and by how much"]
    F(["You"])
    G(["The agent"])

    A -->|"echolot collect"| B
    B -->|"echolot analyze"| C
    C --> D --> F
    C --> E --> G
    E -->|"echolot compare, against an earlier one"| H
    H --> F
    H --> G
```

## Project layout

```
android-project/
├── echolot.yml       ← the project half, committed
├── local.yml         ← device serials, binary path; in .gitignore
└── .echolot/         ← traces, reports, run log, reflect reports; in .gitignore
```

When the project directory is the root of a git checkout, `echolot init` adds
`.echolot/` and `local.yml` to its .gitignore; otherwise it says it did not
and prints the two lines to add. A trace is tens of megabytes and a collect
writes five, so without them the first `git add -A` after a run stages the
lot.

Read it the way you read `gradle.properties` and `local.properties`: one tool
per machine, and the binding to a project living inside that project's
repository.

## Documentation

Start at the [documentation index](https://github.com/grishan0v/echolot/tree/main/docs), or jump straight in:

| | document | about |
|---|---|---|
| 🎬 | [Collecting](https://github.com/grishan0v/echolot/blob/main/docs/collecting.md) | `collect`, the three modes, merging repeats |
| 🔎 | [Analysing](https://github.com/grishan0v/echolot/blob/main/docs/analysing.md) | `probe`, `names`, `domains` — from a trace to a place in the code |
| 🔀 | [Comparing](https://github.com/grishan0v/echolot/blob/main/docs/compare.md) | `compare` — what changed between two reports, and when the repeats support saying so |
| 🧊 | [ANRs](https://github.com/grishan0v/echolot/blob/main/docs/anr.md) | `anr` — reading a report from the field, and measuring a freeze |
| 🏷️ | [Marking](https://github.com/grishan0v/echolot/blob/main/docs/mark.md) | `mark` — first markers for a project with no instrumentation |
| ⚙️ | [Detectors](https://github.com/grishan0v/echolot/blob/main/docs/detectors.md) | writing your own, the context views, self time versus total |
| 📏 | [Calibrating](https://github.com/grishan0v/echolot/blob/main/docs/calibrate.md) | thresholds from healthy runs, why rank beats percentile |
| 🔒 | [Determinism](https://github.com/grishan0v/echolot/blob/main/docs/determinism.md) | the pinned `trace_processor`, `doctor`, the self-check |
| 🤖 | [The agent layer](https://github.com/grishan0v/echolot/blob/main/docs/agent-layer.md) | the `.claude/` layer, and why the loop lives in a subagent |
| 🪞 | [Reflect](https://github.com/grishan0v/echolot/blob/main/docs/reflect.md) | the report over an agent session, for improving the tool |

Agent-facing reference material ships inside the package under
`echolot/claude/skills/echolot/references/` — the report schema, the config
schema, how ART names things, and how to capture a trace by hand.

## Status

**v0.** Everything planned for it is in place.

The detectors were validated against a synthetic trace — 144 checks inside
`doctor`, one per claim — and against live traces from Android 14 (emulator) and Android 13
(Galaxy A51). The naming masks for GC, locks and binder were narrowed against
those real traces, and every narrowing is pinned by a check.

Six are newer than that hardware round. `io_wait`, `anr` and `repeated_work`
have each been run on real traces since — fifteen cold starts of a freshly
installed app on an A51, an ANR raised on purpose on an Android 13 phone, the
traces of the hunts that found a duplicate — and their headers say what those
runs showed. `frame_jank` was built against the pinned `trace_processor` and a
frame timeline written for the purpose — the column names, the jank vocabulary
and where display frames live were all read back out of it rather than
assumed — but no report from it has been compared with a real device's own
frame statistics yet. `main_thread_outlier` was written for a miss recorded on
an A51 and has so far answered only the fixture. `anr_risk` is silent on the
fixture by construction: its bar is the platform's five seconds, and the
fixture is a one-second cold start. Its checks run it there with the bar
lowered, and one holds it to silence at the bar it ships with.

A failed detector never fails the run: the error goes to stderr and into
`report.json`.

### Working on the tool

```bash
pip install -e '.[dev]'
pytest                       # every check, including the ones doctor runs
pytest -k uninstrumented     # one detector's claims, by name
ruff check echolot tests     # the linter, which CI runs beside pytest
```

`doctor` stays dependency-free: it walks the same list itself, because it runs
on a user's laptop where pytest is not installed.

### There is no performance gate, on purpose

An earlier plan had `analyze` exit non-zero against `scenario.budget_ms`, so a
build could fail on a slow run. It is not being built, and this is the reason.

"Did it get slower" is already answered. Macrobenchmark writes percentiles per
iteration right next to the traces echolot collects from it, and comparing a
median against a number is a few lines of anything. An eleventh implementation
of that adds nothing. Worse, detector thresholds on a shared CI runner would
fire on properties of the runner — the same caution this tool already gives
about `runnable_starvation` on a loaded machine.

Where echolot is hard to replace is the other question: *where* the time went.
So the useful shape in CI is the opposite of a gate. Run `echolot doctor -q` as
a precondition — it already answers "does this machine compute correctly" with
an exit code — then `analyze` over the traces the benchmark has already
written, `compare` against yesterday's report, and keep both JSON files as
build artefacts. When someone asks a day later why the nightly regressed, the
window, the thresholds, the evidence and the delta are already sitting next to
the commit: no device, no re-recording.

`compare` exits 0 whatever it finds, for the same reason. It reports; it does
not stand guard.

`scenario.budget_ms` stays in the config. It records what a team considers
acceptable, which is worth writing down whether or not anything enforces it.

CI does hold one gate, and it measures this repository rather than a device:
`pytest` fails when statement coverage drops below the threshold in
`pyproject.toml`. That number comes out the same on every runner, which is
exactly what a trace threshold does not.

## License

[Apache 2.0](https://github.com/grishan0v/echolot/blob/main/LICENSE)
