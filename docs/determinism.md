# Determinism

[← Docs index](README.md) · [README](../README.md)

The whole premise is that the same trace yields the same answer, run after run.
Two things make that true rather than aspirational: the parser version is
pinned, and the pipeline proves itself on a trace whose contents are known.

## `doctor`

```bash
echolot doctor
```

```
## Environment

  python           3.14.7
  platform         Darwin / x86_64
  perfetto         0.57.2
  PyYAML           6.0.3
  rich-argparse    1.8.0
  trace_processor  v56.1

  binary: /Users/you/.local/share/perfetto/prebuilts/trace_processor_shell-99227035e8256d46
  (the name is a SHA-256 prefix: contents verified on download)

## The .claude/ layer in this project

  10 template files: 10 current
  installed by echolot 0.9.0, this is 0.9.0
  the layer is current.

## Self-check on a synthetic trace

  ok    scenario window built from the anchors: 1005 ms
  ok    the right process is picked, the foreign one is dropped
  ...

All 150 checks passed — the pipeline computes correctly.
```

`doctor -q` is the same run in three lines — environment, layer verdict,
self-check tally — plus every failure, with the same exit code. It is for a
subagent that has to confirm the environment before it starts, a CI step, or
anyone piping into `head`; the full output is about ten kilobytes of "ok" that
a second reader in the same session would pay for again.

```
echolot 0.9.0 · trace_processor v56.1 · perfetto 0.57.2 · python 3.14.7
layer: current (10 files)
self-check: 150 of 150 passed
```

Exit code 0/1, and 2 on a first run that could not download trace_processor
(below). No device needed. It takes about fifteen seconds on a laptop,
most of them spent starting trace_processor, which the self-check does about
ten times: once for the report every check reads, then again for each check
that needs a trace or a command of its own. Good both as a CI gate and as the
agent's first action before entering a loop.

Two cases end without a tally. Both exit 1 and go into the run log as a
self-check that did not run — no checks, and one entry under `failed` — so
`echolot` says the self-check did not run rather than that it passed, and
routes the next step back to `doctor`:

- The self-check could not start — a `--tp-binary` pointing at nothing, say.
  `doctor` prints why.
- Python was started with `-O`, or `PYTHONOPTIMIZE` is set in the environment.
  Every check is an `assert` and that flag tells Python to skip them, so the
  tally would stop depending on whether the pipeline computes correctly.
  `doctor` refuses in one line before anything runs, and so does the check
  at the end of `init`.

A first run that cannot download trace_processor ends without a tally too,
and goes into the run log the same way, but it exits 2, with the error every
command gives for it — see [the download](#the-download) below.

This is deliberately **not** a check of "are the dependencies installed". `pip`
already fails loudly, and `TraceSession` carries a clear message about a
missing perfetto. The value is elsewhere:

- the `trace_processor` version becomes visible, and that version defines the
  vocabulary the detectors match on;
- the self-check shows not the presence of tools but the correctness of
  answers.

## The synthetic fixture

`echolot/fixture.py` builds a real Perfetto protobuf trace — `ProcessTree` for
process names, ftrace `sched_switch` for `thread_state`, atrace `print` for
slices. The same format that arrives from a device.

It carries one planted problem per detector plus negative controls: a 5 ms
slice against a 16 ms bar, a 3 ms binder transaction against a 10 ms bar, an
async transaction that must not count as synchronous IPC, slices outside the
window, a foreign process, a well-instrumented thread that must not read as a
blind spot.

`echolot/selftest.py` reads as the fixture's specification in executable form:
what the detectors must find and, just as importantly, what they must not. A
false positive costs an agent more than a miss — it goes off investigating a
problem that does not exist and burns its context window.

Both live in `echolot/` rather than `tests/` on purpose. This is not test
scaffolding but a self-verification asset: `doctor` stands on it, and `doctor`
belongs to the product. A broken environment discovered on the loop's third
round costs the subagent its whole window.

The fixture also pays off in development speed: the SQL edit cycle drops from
ten minutes with a device to one second.

### Running them while working on the tool

`doctor` is what a user runs, and it prints a tally. While changing a detector
you want the opposite — one failure, named, with the claim that stopped
holding:

```bash
pip install -e '.[dev]'
pytest                       # every check, plus the rest of the suite
pytest -k uninstrumented     # one detector's checks
pytest -k frame_jank -x      # and stop at the first that fails
```

The checks themselves do not move: pytest points at the same `CHECKS` list
`doctor` walks, one test each. Nothing in `echolot/` imports pytest, and a user
without it installed still gets all of them through `doctor`.

### The tests that write their own input

`tests/test_*_hypothesis.py` are the exception to "the checks do not move".
They hand a pure function — the table renderer, the timestamp parser, the
config merge, the ANR state vocabulary, the shell-command reader — generated
input rather than a fixture, and check a property of the answer instead of the
answer itself.

Generated input in a project whose whole claim is repeatability needs saying
out loud, so: it is generated, and it is the same every run.
`tests/conftest.py` loads a hypothesis profile with `derandomize=True`, which
picks the examples from a fixed hash of the test rather than from a random
seed, and `database=None`, which stops a machine remembering a failure the
next machine has never seen. Two people on two laptops and the runner all
explore the same inputs, and a red run is reproducible from the diff.

That pin buys repeatability and it costs coverage, in a way worth knowing
before writing one of these. A fixed set of examples is a fixed set of blind
spots: a property that is false for a rare input can pass every run for as
long as the seed holds, and then fail on a hypothesis upgrade that reshuffles
the choice — on a commit that changed nothing. The rule that follows is that
the input a test exists to check is never left to the strategy. It is written
down as an `@example`, and the strategy is weighted to reach it: the table
tests draw `|` from an alphabet of their own, because one character in a
million turns up in some runs and not others.

## What the detectors were checked against

The detectors were validated against a synthetic trace — 150 checks inside
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

## Why the trace_processor version is pinned

`pyproject.toml` holds `perfetto==0.57.2`, and that is not hygiene.

`trace_processor` is not a utility you feed SQL to — it is a **vocabulary**.
The strings the detectors match on are invented by it:

```sql
WHERE state IN ('R', 'R+')          -- runnable_starvation
WHERE state = 'Running'             -- uninstrumented_cpu
WHERE name GLOB 'binder transaction*'
```

None of those appear in the trace we hand it. `'R'` and `'Running'` come from
its scheduler parser; the binder slices come from its binder tracker. A
different TP version means different trace semantics with the SQL unchanged to
the character.

The chain looks like this:

```
pyproject.toml → perfetto package version → manifest → TP binary → results
```

The `perfetto` package carries a manifest with a pinned binary version and a
SHA-256 per architecture; the binary is downloaded once and cached under
`~/.local/share/perfetto`. The manifest is rolled by a separate release tool
with its own numbering, and there is no contract that a patch release keeps the
same binary — so `~=` would not pin the thing the pin exists for. Only `==`
does.

### The download

trace_processor is not in the wheel. The first command that needs it — the
check at the end of `echolot init`, `doctor`, or anything that opens a trace —
fetches the build the manifest names for this OS and CPU, once, and says so on
stderr before it starts:

```
[i] trace_processor v56.1 is not on this machine yet — downloading it once, 13.4 MB, the build the perfetto package pins:
      from https://commondatastorage.googleapis.com/perfetto-luci-artifacts/v56.1/mac-amd64/trace_processor_shell
      into /Users/you/.local/share/perfetto/prebuilts/trace_processor_shell-99227035e8256d46
```

stdout stays the command's result, so the first `names --json` is JSON like
any other. The fetching is perfetto's own: `curl` downloads the file under a
temporary name beside the pinned one, and perfetto checks it against the
SHA-256 in the manifest before giving it the pinned name. That name ends in
the first sixteen hex digits of the hash, and it is how the file is trusted
from then on without being hashed again. The size is 10 to 14 MB, depending
on the platform. `echolot --version` names the pinned version without
downloading anything.

A download that fails is tried once per command, whatever it left under the
temporary name is removed, and the command ends with exit 2 and one error that
names the cause and the ways round it:

```
error: trace_processor v56.1 could not be downloaded: curl exited with status 6 — the server's name did not resolve.
  Every trace is read with it, so nothing that opens one can run until it is here. Any one of these puts it in place:
  - a network that reaches commondatastorage.googleapis.com. Behind a proxy, export HTTPS_PROXY — curl honours it — and run this again;
  - curl, installed and on PATH: the download runs through it;
  - offline, a copy of ~/.local/share/perfetto/prebuilts/trace_processor_shell-99227035e8256d46 from a machine with the same OS and CPU (mac-amd64) that already has it, put at /Users/you/.local/share/perfetto/prebuilts/trace_processor_shell-99227035e8256d46. Keep the name: it carries the hash of the contents, and a file under any other name is not looked at.
```

- **Behind a proxy.** curl takes `HTTPS_PROXY` from the environment. Export it
  before the first run, in the shell or the CI job that makes it.
- **Without curl.** macOS and Windows 10 and later ship it; on a slim Linux
  image it is one package away.
- **Without a network.** Copy the file from a machine with the same OS and CPU
  that has already run echolot: `~/.local/share/perfetto/prebuilts/` there,
  the same directory here, the same name. The name carries the hash of the
  contents, so a file from another platform or another pin lands under a name
  nothing looks for, and cannot be picked up by mistake.
- **In CI.** Cache `~/.local/share/perfetto/prebuilts`, keyed on the perfetto
  version: a new pin is a new file, and every run after the first skips the
  download.

A binary of your own skips all of this — see [your own
binary](#your-own-binary) below.

### A pin without a fixture would be freezing blind

Pinning alone traps you on an old parser, and newer ones handle newer Android
better. That cost is real. What makes it acceptable is that the fixture turns
an upgrade into a reviewable diff:

```
raise the pin → echolot doctor → the checks either pass
                                 or show exactly what moved
```

The TP version is also written into `report.json`. When numbers diverge between
two reports, the first question is whether anything underneath changed, and the
field answers it immediately instead of after an hour of digging.

### Your own binary

A trace_processor of your own goes in one of two ways:

- `--tp-binary <path>`, accepted both before and after the subcommand — for
  one run;
- `toolchain.tp_binary: <path>` in `local.yml`, beside `echolot.yml` — for
  every run on this machine. `init` puts `local.yml` in `.gitignore`, so the
  path stays yours. The key works in `echolot.yml` too, where everyone gets it.

`analyze`, `calibrate`, `names`, `probe` and `doctor` choose in the same
order: the flag, then the config, then the pin. `probe` and `doctor` read the
config in the directory they run from for that key alone, so `probe` opens a
trace with, and `doctor` self-checks, the binary `analyze` would use there. A
config that does not load stops neither: each says so on stderr and goes on
with the flag's binary, or the pin.

`doctor` and the report say which binary ran and who asked for it. With the
path in `local.yml`, `doctor` shows the version the binary reports about
itself, marks the row, and names the source under the path:

```
  trace_processor  …  ← custom binary, the pin in pyproject.toml is bypassed

  binary: /opt/perfetto/trace_processor_shell
  (from toolchain.tp_binary in local.yml)
```

`doctor -q` puts the same on its first line:

```
echolot 0.9.0 · trace_processor … (custom binary from toolchain.tp_binary in local.yml) · …
```

and the footer under the findings in `report.md` reads:

```
trace_processor … (custom binary from toolchain.tp_binary, pin bypassed)
```

With the flag, each of them names `--tp-binary` instead; with neither, there
is no mark at all. `report.json` keeps the same under `toolchain`: `source` is
`pinned`, `--tp-binary` or `toolchain.tp_binary`, and `binary` is the path
that ran.

### What the pin does not solve

- **The device is not pinned.** How ART names GC on a given Android version is
  a separate axis, handled by `names` and the config masks.
- **Team members do not get identical binaries.** mac-arm64 and linux-amd64 are
  different files of the same version. Results should agree, but that is
  Perfetto's promise, not ours.
- **A binary of your own bypasses it entirely**, whichever way it came in.
  Which is why the report records what actually ran, and who asked for it,
  rather than what was supposed to.

## Where determinism ends

Steps `collect` and `analyze` contain no model at all. The model enters only
when reading the report and deciding where to dig, and only with already
compressed data.

That is also what makes the tool useful without any AI: the same CLI hangs in
CI and catches regressions for zero tokens.
