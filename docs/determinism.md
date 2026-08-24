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

  python           3.14.4
  platform         Darwin / x86_64
  perfetto         0.57.2
  PyYAML           6.0.3
  trace_processor  v56.1

  binary: ~/.local/share/perfetto/prebuilts/trace_processor_shell-99227035e8256d46
  (the name is a SHA-256 prefix: contents verified on download)

## The .claude/ layer in this project

  10 template files: 10 current
  the layer is current.

## Self-check on a synthetic trace

  ok    scenario window built from the anchors: 1005 ms
  ...
  All 105 checks passed — the pipeline computes correctly.
```

`doctor -q` is the same run in three lines — environment, layer verdict,
self-check tally — plus every failure, with the same exit code. It is for a
subagent that has to confirm the environment before it starts, a CI step, or
anyone piping into `head`; the full output is six kilobytes of "ok" that a
second reader in the same session would pay for again.

```
echolot 0.5.3 · trace_processor v56.1 · perfetto 0.57.2 · python 3.14.7
layer: STALE — 8 differs, 1 missing → `echolot init --force`
self-check: 105 of 105 passed
```

Exit code 0/1. No device needed, one second — good both as a CI gate and as
the agent's first action before entering a loop.

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

Your own binary goes in via `--tp-binary`, accepted both before and after the
subcommand. The report then marks that the pin was bypassed.

### What the pin does not solve

- **The device is not pinned.** How ART names GC on a given Android version is
  a separate axis, handled by `names` and the config masks.
- **Team members do not get identical binaries.** mac-arm64 and linux-amd64 are
  different files of the same version. Results should agree, but that is
  Perfetto's promise, not ours.
- **`--tp-binary` bypasses it entirely.** Which is why the report records what
  actually ran, rather than what was supposed to.

## Where determinism ends

Steps `collect` and `analyze` contain no model at all. The model enters only
when reading the report and deciding where to dig, and only with already
compressed data.

That is also what makes the tool useful without any AI: the same CLI hangs in
CI and catches regressions for zero tokens.
