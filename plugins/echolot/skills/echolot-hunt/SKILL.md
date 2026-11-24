---
name: echolot-hunt
description: Find the cause of one Android performance regression with echolot. Opens an investigation, hands the loop to a subagent that starts clean, and closes the investigation with its answer. The echolot skill routes here once the config exists; use it directly when the human names a regression to hunt.
---

# The hunt

The main thread's part; `echolot guide hunt` has it at length.

1. `echolot doctor -q`, on its own. Non-zero: show what failed, and stop.
   If a sandbox refused a port, ask to run it outside the sandbox first.
2. **Three facts, in the human's words**: what regressed against what ("it was
   3 s, now 7 s"), which traces show it, and **after which change**. Ask for
   the change even when the rest is clear; "unknown" is an answer. Then a
   reference, an earlier build's traces or a build to record first
   (`echolot guide hunt`, "A reference"): with none, the answer is where this
   build spends its time, not what changed. Say so.
3. **The scenario**, as `echolot status` lists them: the words fit one by its
   name, window and runner, even when there is one; fitting none (a scroll,
   only a cold start), add one first, `echolot guide setup`, "Adding a
   scenario". Several: ask when unsure; add `--scenario <name>` below.
4. **Open the investigation before anything is recorded**, unless the human
   chose to carry on the open one; else ask, and if it came to something,
   `echolot hunt --done "<what>"` it first.

   ```bash
   echolot hunt "<what regressed, in their words>" --since "<the change, or unknown>"
   ```

   It moves the scenario's loose traces aside (stderr says where): they are
   this question's evidence only if the human recorded them for it before
   asking. Exit 2: `echolot.yml` does not load; show why, stop. The last
   investigation's markers go first: `echolot mark --remove`, and by hand the
   lines it lists. No traces recorded for this question:
   `echolot collect -c echolot.yml -n 5 --hunt <n>`, `-n 1` after a runner
   change.
5. **Hand the loop to a subagent that starts with none of this conversation**,
   this brief as its first message, filled in. `#<n>` is `opened #<n>` (or
   `echolot hunt --list`'s `→` row); `Thresholds:` reads the `thresholds`
   column of `echolot report`: calibrated on the runs that hold the
   regression means `--defaults`.

   ```text
   Run `echolot guide loop` first and follow it: it is your guide to the loop.
   Traces: <each trace file by name, from the directory `echolot hunt` set aside or from .echolot/traces>
   Regressed: <what, against what>, after <the change, or "unknown">
   Reference: <the earlier build's traces, each file by name, and which build | none>
   Investigation #<n> is open: pass `--hunt <n>` to every `collect` and `analyze`, so `echolot compare` reads what they file.
   Thresholds: <the config's can be trusted | start with `echolot analyze --defaults`>
   Doctor passed at <time>: do not run it again.
   Instrumentation: <what `echolot domains --root .` found | none: start with `echolot mark`, not with reading the app>
   ```

   **Do not run `echolot guide loop` yourself**: it is the subagent's. Then
   **wait for its conclusion**: a turn that ends while it runs may get no
   next one. With no subagent tool, even among those listed on request, the
   loop is yours: `echolot guide loop`.
6. **When the conclusion comes back, close the investigation first**, every
   time, then answer the human:

   ```bash
   echolot hunt --done --verdict .echolot/verdict.json
   ```

   Exit 2 is a refusal that records nothing: send its entries back to the
   subagent and run it again (without `--done` it only checks). With no file:
   `echolot hunt --done "<place> — <what happens there>; confidence <high|medium|low>"`.

   Show the conclusion as it is; unsure the markers are gone, check as
   `echolot guide hunt` says. Say a low confidence or no reference out loud;
   a device finding wants a run on real hardware.
