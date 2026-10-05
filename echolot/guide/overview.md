# echolot — how to work with it

You are an AI agent in a repository that uses echolot. This is what the tool
is for and the order to use it in. Printed by the package itself, so it can
never be out of step with the version installed.

## The one rule

**Never open the trace yourself.** No hand-rolled TraceProcessor, no ad-hoc
SQL, no reading `.perfetto-trace`. A trace is tens of megabytes and hundreds of
thousands of slices; everything you need arrives as a table of about twenty
rows.

Live proportions: an 81 MB trace with 475k slices becomes a 14 KB
`report.json` in about five seconds.

If the report seems to be missing something, that is not a reason to open the
trace. It is a reason to fix the config (anchors that did not match, the wrong
process), to add instrumentation and record again, or to add a detector.

## Ask the tool where you are

Do not guess the next step from the state of the repository. The tool knows:

```bash
echolot                 # where things stand, and one line saying what is next
echolot status --next   # the same as one word, for switching on
```

| `next` | what to do |
|---|---|
| `upgrade` | the `.claude/` layer here was written by a newer echolot than the one installed, or its manifest names a version the installed one cannot read, and the installed one writes nothing into it. Show the `layer` line to the human as it is — it names both versions and the command to upgrade — and stop. Do not run `echolot init`: it refuses |
| `init` | run `echolot init`, then run `echolot` again. Brought here by the echolot plugin's skills, it is `echolot init --for plugin`: the plugin brings the skills, and `.claude/` would be a second copy. In Codex it is `echolot init --for plugin,codex`, which also writes the rule that lets echolot out of the sandbox |
| `doctor` | run `echolot doctor`, show what failed, stop — no report is trustworthy until it passes |
| `setup` | build `echolot.yml` — run `echolot guide setup` |
| `fix-config` | show the parse error, ask the human to fix `echolot.yml`, stop |
| `resume-or-new` | an investigation was left open. Show `echolot hunt` and ask the human: carry on (`echolot hunt --resume`, then `echolot guide hunt` inside it), or start a new one (`echolot guide hunt`, which opens it) |
| `init-force` | files in `.claude/` may carry edits made here, and `echolot init --all` would overwrite them. Run `echolot init`, show the files it marks `≠`, and ask the human: overwrite them (`echolot init --all`), or keep them. Either way, go on as for `hunt` |
| `fix-settings` | `.claude/settings.json` does not parse, and no flag of `init` can fix it. Show the `layer` line — it says what to merge in — and tell the human to fix the file by hand, then go on as for `hunt` |
| `hunt` | find the regression — run `echolot guide hunt` |

## The order of work

```bash
echolot doctor -q                          # does this environment compute correctly?
echolot collect -c echolot.yml -n 5        # capture traces, when there are none
echolot analyze <trace...> -c echolot.yml  # the report
```

`analyze` writes `.echolot/out/report.json` for you and `report.md` for humans.
**Read the json** — it has a stable schema — through `echolot report`: what
fired, `--detector <id> --top 5` for one detector's rows with the evidence
kept short, `--window`, `--markers`, `--json` for any of them. Cutting the
file up with jq puts a window's worth of json into your context for a line.

```bash
echolot compare                            # what moved since the previous round
echolot compare --hunt <n>                 # an investigation's first report against its last
echolot compare <before.json> <after.json> # or name the two reports
```

`compare` answers the other half of the question the report cannot: which rows
appeared, which grew, and whether the repeats support calling that a change.
Read `holds` before acting on a row — `false` means the runs disagree among
themselves by more than the row moved.

## When a sandbox is in the way

echolot needs a port on localhost, where trace_processor runs, and the adb
server, which is how `collect` reaches the phone. A sandbox with no network
refuses both, and Codex runs commands in one by default. `doctor`, `analyze`
and `collect` then say so and name the way out; nothing is wrong with the
install. If your client lets you ask to run a command outside its sandbox, ask
for the echolot command, with that message as the reason. Otherwise show the
message to the human and stop: letting echolot out is theirs to approve.

- Codex: approve running `echolot` outside the sandbox when it asks, or let
  it out for good with the rule `init` writes into
  `.codex/rules/echolot.rules` when `codex` is among its agents — the `codex`
  line of `echolot` names the command. `init` has to run outside the sandbox
  for that, since Codex keeps `.codex/` read-only inside it. Codex reads the
  rule when a session starts, in a project it trusts, and a rule covers a
  command line that is echolot alone: run each `echolot` command on its own,
  with the trace files named. `git status && echolot doctor -q` runs whole
  inside the sandbox, and so does a `.echolot/traces/*.perfetto-trace` —
  Codex does not look inside a line with a glob.
- Claude Code, with its sandbox turned on: `"echolot *"` in
  `sandbox.excludedCommands`.

Letting the one command out is the fix. Turning the network on for every
command in the session is wider than it needs to be, and the first download of
trace_processor still has nowhere to go: perfetto keeps it outside the
workspace.

## Reading the report

Three things you will get wrong without being told.

**Silent detectors matter as much as firing ones.** They stay in the report
with empty `rows`. Silence means that ground was checked and is clean — do not
go there.

**`self_ms` versus `total_ms`.** Self time, with children subtracted, is where
the time actually went. `traversal` with `total_ms: 354` and `self_ms: 79` does
almost nothing itself; dig into its children. Never add `total_ms` across rows:
they nest inside one another. Nor `self_ms` across detectors: a disk wait
inside a slice is in both. How much of the window the findings cover is
`window.main_thread.in_rows_pct`, each moment counted once.

**Warnings inside `window`.** If `start_anchor.matches == 0` or
`end_anchor.matches == 0` the window ran to the edge of the trace and none of
the numbers are about your scenario.
Same for `process_alternatives` — you may be analysing the wrong process. Fix
the config rather than hunting a problem.

**Silence is relative to the thresholds.** The report's `config` field and
`detectors[].params_source` say whether the numbers are the shipped defaults or
calibrated ones. Calibrated on the very runs that hold the regression means the
bar sits above it: look with `echolot analyze … --defaults` before calling a
run clean.

**`frame_jank` is about single frames, not totals.** Most of the others
aggregate by name and gate on sums, which is right for "cold start got slower"
and blind to a heavy tail: one 86 ms frame among thousands disappears into
every sum there is. This one reads SurfaceFlinger's per-frame record instead,
so it needs no instrumentation and answers a question the sums cannot.

Its `total_ms` and `max_ms` are time **past the deadline**; the frame's own
length is in `detail`, which is where a benchmark's percentiles can be matched.
`detail` also leads with the platform's verdict on whose deadline was missed —
`Self Jank` is the app, `Other Jank` is the compositor and is not something to
go into the code over.

Its silence is ambiguous: no bad frames, and no frame timeline in the trace at
all, look the same. Android 11 and below have none, and neither does a capture
that did not ask for it. Check before calling a scenario smooth.

## From a finding to the code

1. A firing detector gives a `location` — a slice or thread name.
   A row with `code` has already been placed: `places[].file` and `.line`
   name the method that waited for a lock and the one holding it, or the
   class a View slice is. Open that; skip the grep.
2. The `domains` section of `echolot.yml` maps that name to a module and file.
3. Not in `domains`? Run `echolot domains --root .` — it maps literals inside
   `trace("...")` and names kept in a `const val` and passed through the
   project's own wrapper; a hint ending in `via X` names what to grep for at
   that line. Still nothing? Grep the repository for the slice name: a literal
   survives minification and is found exactly, and a constant's declaration
   is one grep away from its calls.
4. Nothing found? The slice is most likely a system one (`bindApplication`,
   `Choreographer#doFrame`, `binder transaction`).

When `uninstrumented_cpu` fires there is no code behind the finding by
definition: the thread burned CPU with no instrumentation. That is an address
for adding `trace{}`, not the location of a bug. A recording with callstack
samples narrows the address in the same round: the end of the row's evidence
names what ran on the stacks, then `ours:` and the nearest method of the
project's own under it, and `code` gives that method's file. `ours: none` is
work a pool ran with its caller off the stack; `ours: cut` is a stack that
ended in the framework before anything of ours.

## Watch your context

The loop generates a lot of raw output — reports, repository searches,
instrumentation diffs, several rounds. If your host can start a subagent, hand
the loop to one, with none of this conversation: `echolot guide hunt` says
what to give it, and `echolot guide loop` is its instructions. Then wait for it
rather than ending your turn while it runs. Its conclusion is the whole answer,
and a host that only hands it back on a later turn may not get one. Return the
conclusion alone. If it cannot run a separate context,
work in short passes and keep raw output out of the conversation: read
`report.json`, quote the two or three rows that matter, and drop the rest. A
window filled with raw output is where the instability this tool exists to
remove comes back.

**`main_thread_outlier` is not `main_thread_block` again.** One gates on the
sum for a name and answers "where did the time go"; the other gates on a
single occurrence against the median for that same name and answers "which one
was out of line". A name in both is telling you two things.

They lead to different places in the code. Expensive every time means the fix
is in that work. Usually fine and once not means the cause is the state it hit
that once — a cold cache, a lock, a first-run path. `detail` carries the median
and how many occurrences it came from, which is what to hold a benchmark's
percentiles against.

## Reporting back on the tool itself

When the question is about echolot rather than the app — how a session went,
where the tool got in the way — that is `echolot reflect --last`, run from the
project you worked in.

Only Claude Code has a reader for its transcripts. From anywhere else the
report is built from the tool's own run log, `.echolot/log/runs.jsonl`: which
commands ran, when, for how long, with what exit code. It is smaller, and it
names under **Not checked** every check it could not make. Read that section —
a check listed there found nothing because it had nothing to look at, which is
not the same as finding nothing wrong.

## Boundaries

The tool **localises one specific regression**: you know "it was 3 s, now it is
7 s" and need to find where. It does not search for the unknown across a pile
of traces.

Thresholds are tied to a device and a scenario. When either changes, run
`echolot calibrate` on known-healthy runs instead of nudging numbers by hand.

## More

```bash
echolot reflect --last  # how this session went, and what to change in the tool
echolot compare --help  # the forms it takes and the floor it uses
echolot guide setup     # building echolot.yml for a project that has none
echolot guide hunt      # a hunt: the facts, the investigation, handing over the loop
echolot guide loop      # the loop itself: from a report down to a place in the code
echolot guide report    # report.json, field by field; also config, naming, collect
echolot guide anr       # the app stopped answering: reading an ANR report, and measuring one
echolot explain         # the detectors and their parameters
echolot --help          # every command, grouped by who runs it
```
