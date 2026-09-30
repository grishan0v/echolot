<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/logo-dark.png">
    <img alt="echolot" src="https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/logo-light.png" width="429" height="128">
  </picture>
</p>
<p align="center">
  <b>Turns a huge Android trace into 20 rows of facts an AI agent can actually use.</b>
</p>
<p align="center">
  <a href="https://github.com/grishan0v/echolot/actions/workflows/checks.yml"><img alt="checks" src="https://github.com/grishan0v/echolot/actions/workflows/checks.yml/badge.svg?branch=main"></a>
  <a href="https://pypi.org/project/echolot/"><img alt="PyPI" src="https://img.shields.io/pypi/v/echolot.svg?color=%236e7781"></a>
  <a href="https://pypi.org/project/echolot/"><img alt="Python versions" src="https://img.shields.io/badge/3.10--3.14-6e7781?logo=python&logoColor=white"></a>
  <a href="https://github.com/grishan0v/echolot/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache%202.0-6e7781.svg"></a>
</p>
<br>

<p>
  <picture>
    <source media="(max-width: 767px)" srcset="https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/hero-narrow.svg">
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/hero-dark.svg">
    <img alt="From a huge trace to the line to fix: echolot turns the trace into twenty rows of facts, and your agent follows one of them to the line in the code" src="https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/hero-light.svg">
  </picture>
</p>

- **Finds the line to fix.** From a slow screen to a file and line, with the
  numbers that prove it.
- **Same answer, every run.** A pinned `trace_processor`: the same trace always
  gives the same report.
- **No tracing code needed.** Works on apps with zero `trace {}` calls; it
  places temporary markers itself.
- **Works with your agent.** Claude Code out of the box; Cursor, Codex and
  others via `echolot guide`.

<p>
  <img alt="A condensed /echolot session: the question, three rounds of recording and reading, and the answer with its place in the code" src="https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/session.svg" width="880">
</p>

**Contents** · [Quick start](#quick-start) · [How it works](#how-it-works) · [What it saves](#what-it-saves) · [What you get](#what-you-get) · [What changed](#what-changed)

**Reference** · [Detectors](#detectors) · [Commands](#commands) · [Requirements](#requirements) · [Project layout](#project-layout) · [Documentation](#documentation) · [Status](#status)

## Quick start

You need Python 3.10+, `adb`, and a phone or emulator with USB debugging on.
The full list is under [Requirements](#requirements).

### 1. Install

```bash
pipx install echolot
```

`echolot --version` names what you got. Installed with `pip` into an
environment whose scripts are not on `PATH`, `python -m echolot` is the same
command.

### 2. Set up your project

```bash
cd ~/my-app && echolot init
```

This installs the `.claude/` layer — a skill, the `perf-hunter` agent and three
commands — and checks that this machine computes traces correctly.

### 3. Open the agent and type one word

```
/echolot                          reads the state, does whatever is next
/echolot why is cold start slow   hunt, with that as the question
/echolot init | setup | hunt | reflect | doctor
```

The first run builds `echolot.yml` from your repository and a probe trace,
asking you four questions along the way. Every run after that hunts down the
regression you describe.

### What to ask

Describe the problem the way you would to a colleague:

| the problem | what to type | what it needs |
|---|---|---|
| Cold start got slower | `/echolot why is cold start slow` | — |
| Scrolling stutters | `/echolot the feed janks on scroll` | a scroll scenario in `echolot.yml`, which holds one scenario at a time; `frame_jank` needs Android 12+ |
| "App isn't responding" | `/echolot the app froze, here is anr.txt` | the report: an export from Crashlytics or Play Console, or `dumpsys dropbox` |
| The nightly benchmark got slower | `echolot analyze` over its traces, then `echolot compare last-night.json` | wired by hand for now; the shape, and why it never fails a build, is in [Comparing](https://github.com/grishan0v/echolot/blob/main/docs/compare.md) |

### Coming back later

`echolot` on its own prints where the project stands and the next step. After
upgrading the package, run `echolot init` again: it brings the `.claude/` layer
up to date and leaves the files you edited alone.

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

## How it works

echolot runs thirteen SQL detectors over a Perfetto trace and returns about
twenty rows: where the time went, how much of it, and the evidence behind each
claim. Same trace in, same report out: the `trace_processor` version is pinned.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/loop-dark.svg">
    <img alt="The hunt as a loop: echolot records, analyzes and compares the same way every time; the agent reads the rows, marks one blind spot a round, and names the place in the code" src="https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/loop-light.svg" width="880">
  </picture>
</p>

The recording, the detectors and the comparison are scripts that run the same
way every time. In each round the agent makes one decision: where to look
next. The loop ends when the report names a place in the code, or when the
rounds run out.

| where you run it | how |
|---|---|
| Claude Code | the full loop: `echolot init`, then `/echolot`. The agent records, reads the report and walks down to the code |
| Claude Code or Codex, as a plugin | `/plugin marketplace add grishan0v/echolot` in Claude Code, `codex plugin marketplace add grishan0v/echolot` in Codex. The plugin's door sets the project up itself, and the loop runs in a subagent in both |
| Cursor, Codex, other agents | `echolot init` points them at the tool, and `echolot guide` tells them how to work with it. For Codex it also writes the rule that lets echolot out of the sandbox. The loop runs in your main context, so keep the passes short |
| a shell or CI | the pipeline commands under [Without an agent](#without-an-agent) |

### The investigation

Each question you bring gets an investigation. `echolot hunt "cold start was
3s, now 7s" --since "the tab redesign"` opens one, sets the previous traces
aside without deleting them, and files every round of traces and every report
under it, so a question asked weeks ago still knows what was measured to
answer it. `/echolot` opens and closes them for you. The commands, and what
each one keeps, are in [The agent layer](https://github.com/grishan0v/echolot/blob/main/docs/agent-layer.md).

## What it saves

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/versus-dark.svg">
    <img alt="One bug, one model: a hunt costs less with echolot than without it, and follows one fixed loop instead of a new approach every run" src="https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/versus-light.svg" width="880">
  </picture>
</p>

Cost is the measure because it includes the work of the agent's subagent:
token counts leave that out, and time went both ways across the four models.

## What you get

A **Marker Report**: what was measured, then one section per detector that
fired. The silent ones are named too.

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
Detectors fired: **5 of 13**
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

**Silent:** anr, anr_risk, app_init, binder_txn, gc_pressure, io_wait, repeated_work, runnable_starvation

<sub>trace_processor v56.1</sub>
```

</details>

`report.md` is for you, `report.json` for the agent.
An 81 MB trace with 475k slices comes out as a 14 KB `report.json` in about
five seconds.

## What changed

The report says where the time went in one set of traces. The question people
arrive with has a second half — *it was 3 s, now it is 7 s* — and that needs
two sets.

```bash
echolot compare                       # inside an investigation: previous round vs latest
echolot compare old.json new.json     # or name them
```

One table, sorted by how far each row moved. The top row is usually the answer.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/compare-dark.svg">
    <img alt="The sample comparison: a thread that is new and has no slices, a function that got slower inside and holds, and one called more often whose move does not hold yet" src="https://raw.githubusercontent.com/grishan0v/echolot/main/docs/assets/compare-light.svg" width="880">
  </picture>
</p>

| column | what it says |
|---|---|
| `Δ` | how far the median moved, and by what factor |
| `N` | how many times it ran, before and after: called more often is a different bug from slower inside |
| `Holds` | the range the move lies in, 95% sure. `yes` when the range stays on one side of zero; `no` asks for another round of `collect`, and says about how many runs a side would settle it |

A report built against other thresholds, or on a device whose clock moved or
throttled between the rounds, is named above the table. See
[Comparing](https://github.com/grishan0v/echolot/blob/main/docs/compare.md).

## Detectors

| detector | what it catches |
|---|---|
| `main_thread_block` | where the main thread spent its time, by self time |
| `app_init` | what ran after the Application was created, before the first Activity: initializers by name, and the ContentProviders and `Application.onCreate` nobody traced |
| `gc_pressure` | frequent or expensive GC, and waits on allocation |
| `monitor_contention` | lock contention, with the owner's tid as evidence |
| `binder_txn` | long synchronous IPC, and death by a thousand cuts |
| `runnable_starvation` | thread ready to run but preempted on CPU |
| `uninstrumented_cpu` | **threads burning CPU with no instrumentation**, and what they ran when the recording sampled callstacks |
| `frame_jank` | frames that missed their deadline, and whose fault it was |
| `anr_risk` | stretches where the main thread never got back to the message queue |
| `anr` | ANRs the system recorded during the trace, with its own reason |
| `main_thread_outlier` | one occurrence far longer than that work usually takes |
| `repeated_work` | the same work reached from more than one caller, costing about the same both times |
| `io_wait` | **threads the kernel parked waiting for a block device** |

`uninstrumented_cpu`, `io_wait` and `frame_jank` find a problem where nobody
wrote a `trace{}` call. What each detector sees is in
[Analysing](https://github.com/grishan0v/echolot/blob/main/docs/analysing.md), and writing your own in
[Detectors](https://github.com/grishan0v/echolot/blob/main/docs/detectors.md).

## Commands

`echolot --help` lists every command in four groups. Every verb after
`/echolot` is an `echolot` command of the same name, except `setup`, which
only the agent has. The pipeline — `collect`, `analyze`, `compare` — is under
[Without an agent](#without-an-agent).

### Yours

| command | what it does |
|---|---|
| `echolot` | where this project stands, and the next step |
| `echolot init` | install or update the `.claude/` layer; .gitignore, and checks the environment |
| `echolot hunt "<what regressed>"` | open an investigation — see [The investigation](#the-investigation) |
| `echolot doctor` | environment + self-check on a synthetic trace; exit 0 when every check passes, 1 when one fails or the self-check cannot run, 2 when trace_processor cannot be downloaded; `-q` for three lines |

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

## Requirements

| | |
|---|---|
| **Python** | 3.10 or newer |
| **`curl`** | on `PATH` — the one download in the next row goes through it |
| **`trace_processor`** *(fetched once)* | downloaded by the first command that needs it, usually `echolot init`: 10–14 MB, checked against its SHA-256. Behind a proxy or offline, see [Determinism](https://github.com/grishan0v/echolot/blob/main/docs/determinism.md) |
| **`adb`** | on `PATH` — ships in the Android SDK platform-tools |
| **Device** | a phone or emulator with USB debugging on |
| **Agent** *(optional)* | [Claude Code](https://claude.com/claude-code) for the full workflow; Cursor, Codex and others via `echolot guide` |
| **Android 12+** *(for one detector)* | `frame_jank` reads SurfaceFlinger's frame timeline. Older devices do not have it, and the detector is then silent — which reads exactly like "no bad frames" |

Validated on Android 14 (emulator) and Android 13 (Galaxy A51).

## Project layout

```
android-project/
├── echolot.yml       ← the project half, committed
├── local.yml         ← device serials, binary path; in .gitignore
└── .echolot/         ← traces, reports, run log, reflect reports; in .gitignore
```

`echolot init` adds the last two to `.gitignore` at the root of a git checkout,
and otherwise prints the two lines to add: a trace is tens of megabytes.

## Documentation

Start at the [documentation index](https://github.com/grishan0v/echolot/tree/main/docs), or jump straight in:

| | document | about |
|---|---|---|
| 🎬 | [Collecting](https://github.com/grishan0v/echolot/blob/main/docs/collecting.md) | `collect`, the three modes, callstack sampling, merging repeats |
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

**v0.** Everything planned for it is in place. What each detector was checked
against, from the synthetic fixture to live traces, is in
[Determinism](https://github.com/grishan0v/echolot/blob/main/docs/determinism.md).

A failed detector never fails the run: the error goes to stderr and into
`report.json`. Working on the tool itself starts at `CONTRIBUTING.md`, which
GitHub shows as a tab beside this README.

## License

[Apache 2.0](https://github.com/grishan0v/echolot/blob/main/LICENSE)
