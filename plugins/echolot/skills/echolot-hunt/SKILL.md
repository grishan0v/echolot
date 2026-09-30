---
name: echolot-hunt
description: Find the cause of one Android performance regression with echolot. Opens an investigation, hands the loop to a subagent that starts clean, and closes the investigation with its answer. The echolot skill routes here once the config exists; use it directly when the human names a regression to hunt.
---

# The hunt

Run `echolot guide hunt` and follow it. The installed echolot prints it in
full; this is the order, with the steps that go wrong when they are skipped.

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
   reads from it. Traces it moved aside for this question are still its
   evidence: the guide says where they went.
4. **Hand the loop to a subagent that starts with none of this conversation.**
   The loop fills a window with raw output within two rounds; the subagent
   keeps it and returns only its conclusion. Give it what `echolot guide hunt`
   lists, beginning with: run `echolot guide loop` first and follow it. That
   guide is the subagent's to read; reading it here only fills this window. Then
   **wait for its conclusion**. Do not end your turn while it runs: a turn that
   ends on "I will come back with its output" may never get a next one. If
   your host cannot start a subagent, run `echolot guide loop` yourself, in
   short passes.
5. **When the conclusion comes back, close the investigation first**, every
   time and whatever it came to, and only then answer the human. Left for
   after the answer, it is the step that gets skipped:

   ```bash
   echolot hunt --done "<place> — <what happens there>; confidence <high|medium|low>"
   ```

   Then show the conclusion as it is.
