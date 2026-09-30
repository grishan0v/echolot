---
name: echolot-hunt
description: Find the cause of one Android performance regression with echolot. Opens an investigation, hands the loop to a subagent that starts clean, and closes the investigation with its answer. The echolot skill routes here once the config exists; use it directly when the human names a regression to hunt.
---

# The hunt

This is the main thread's part, whole: `echolot guide hunt` prints the same
for a host that does not list this skill, and the loop's guide is the
subagent's.

1. `echolot doctor -q`, on its own. A non-zero exit: show what failed, and
   stop. If it says a sandbox refused a port, ask to run it outside the
   sandbox, as the message says, before you stop.
2. **Three facts, in the human's words**: what regressed against what ("it was
   3 s, now 7 s"), which traces show it, and **after which change**. Ask for
   the change even when the rest is clear. "Unknown" is an answer; not asking
   is not.
3. **Open the investigation before anything is recorded**, unless you are
   carrying on the one that is open:

   ```bash
   echolot hunt "<what regressed, in their words>" --since "<the change, or unknown>"
   ```

   Every later `collect` and `analyze` is filed under it, and `echolot compare`
   reads from it. Traces it moved aside are this question's evidence; stderr
   names the directory. Exit 2 means `echolot.yml` does not load: show the
   error and stop. No traces at all: `echolot collect -c echolot.yml -n 5`.
4. **Hand the loop to a subagent that starts with none of this conversation**,
   with this brief as its first message, filled in:

   ```text
   Run `echolot guide loop` first and follow it: it is your guide to the loop.
   Traces: <each trace file by name, from the directory `echolot hunt` set aside or from .echolot/traces>
   Regressed: <what, against what>, after <the change, or "unknown">
   Investigation #<n> is open: every `analyze` you run is filed under it, and `echolot compare` reads from it.
   Thresholds: <the config's can be trusted | start with `echolot analyze --defaults`>
   Doctor passed at <time>: do not run it again.
   Instrumentation: <what `echolot domains --root .` found | none: start with `echolot mark`, not with reading the app>
   ```

   **Do not run `echolot guide loop` yourself**: it is the subagent's, and here
   it only fills this window. Then **wait for its conclusion**. Do not end
   your turn while it runs: a turn that ends on "I will come back with its
   output" may never get a next one. If your host cannot start a subagent,
   the loop is yours: `echolot guide loop`, in short passes.
5. **When the conclusion comes back, close the investigation first**, every
   time and whatever it came to, and only then answer the human. Left for
   after the answer, it is the step that gets skipped:

   ```bash
   echolot hunt --done "<place> — <what happens there>; confidence <high|medium|low>"
   ```

   Then show the conclusion as it is.
