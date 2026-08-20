# echolot documentation

[← back to the README](../README.md)

Ten documents. If you are here for the first time, read them in the order of
the workflow: **collect → mark → analyse → compare → calibrate**. The rest
explains why the tool is built the way it is.

---

## Using it

The path from a device to an answer, in the order you walk it.

| document | read it when |
|---|---|
| 🎬 **[Collecting traces](collecting.md)** | you need traces — the three modes (`launch`, `command`, `gradle`), what the trace config must contain, and why repeats are merged rather than averaged |
| 🏷️ **[Marking](mark.md)** | your project has no instrumentation and the report has nothing to attach findings to — `mark` writes the first markers from the platform's vocabulary, `--apply` / `--remove` |
| 🔎 **[Analysing](analysing.md)** | you have a trace and need a place in the code — `probe`, `names`, `domains`, and the slice-to-code map |
| 🔀 **[Comparing](compare.md)** | you have two sets of traces and need the difference — what appeared, what grew, and whether the repeats support calling it a change |
| 📏 **[Calibrating](calibrate.md)** | the shipped thresholds fire on everything, or on nothing — derive them from known-healthy runs instead, and why rank beats percentile |

## Understanding it

Why the answers can be trusted, and what the tool refuses to do.

| document | about |
|---|---|
| 🔒 **[Determinism](determinism.md)** | the pinned `trace_processor`, the synthetic fixture, what `doctor` actually checks, and where determinism ends |
| 🤖 **[The agent layer](agent-layer.md)** | what `echolot init` installs, why a CLI rather than an MCP server, why the loop lives in a subagent |

## Extending it

| document | about |
|---|---|
| ⚙️ **[Detectors](detectors.md)** | writing your own — the context views, why two duration columns are not a duplicate, self time versus total |
| 🪞 **[Reflect](reflect.md)** | the same kind of report, over an agent session: how the tool was used, where it got in the way |

## Maintaining it

| document | about |
|---|---|
| 📦 **[Publishing](publishing.md)** | cutting a release — the tag, Trusted Publishing, checking artefacts locally |

---

## Also shipped inside the package

Reference material the agent reads, under
`echolot/claude/skills/echolot/references/`:

| file | about |
|---|---|
| `report.md` | the Marker Report schema — the shape of `report.json` |
| `config.md` | the `echolot.yml` schema, field by field |
| `naming.md` | how ART names things, and what the detector masks match |
| `collect.md` | capturing a trace by hand, without `collect` |
