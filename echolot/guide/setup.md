# Building `echolot.yml`

Run this when `echolot status --next` says `setup`. The goal is a config that
names the right process and a scenario window that actually matches — a wrong
one makes every later report quietly meaningless.

## What the config has to get right

| section | what it decides |
|---|---|
| `project.process` | which process is measured; an app usually has several |
| `scenario.start` / `end` | the window. Anchors are globs over slice names |
| `runner` | who drives the scenario: `launch`, `command` or `gradle` |
| `domains` | slice name → module and file, for turning a finding into a place |

## The order

**1. A probe trace.** You cannot write anchors for a trace you have not seen.

```bash
echolot collect -c echolot.yml -n 1     # if a rough config exists
```

**2. Look inside it.**

```bash
echolot probe <trace> --process '<package>*'
```

That gives processes, threads by CPU, and candidate anchors. Pick the process
deliberately: `com.example.app*` also catches `:pushservice` and `:webview`.

**3. Check how this app names things.**

```bash
echolot names <trace> --process '<package>*'
```

Slice name inventory, and what the detector masks currently see.

**4. Fill `domains` mechanically.**

```bash
echolot domains --root .
```

A slice name is a string literal that survives minification, so the map can be
assembled by scanning. What is left for a human is fixing the wording.

**5. Verify the window.** Run `echolot analyze` and check `window` in
`report.json`: `start_anchor.matches` must be non-zero, and `duration_ms` must
look like the scenario you meant. If the anchor missed, the window silently
expanded to the whole trace.

## Ask the human, do not invent

Four things cannot be read off a trace. Ask, and put the answers in the config:

1. **which scenario** matters — cold start, a screen, a list
2. **which process**, when the probe shows several plausible ones
3. **where the scenario ends** — first frame, data on screen, interaction ready
4. **what counts as acceptable** — goes into `scenario.budget_ms` as a record

Mark anything you inferred rather than were told. The shipped example config
uses `_source:` and `_evidence:` keys next to a value for exactly this.

## When you are done

```bash
echolot                 # should now say next: hunt
```

`echolot.yml` is committed — it describes the project. Device serials and the
path to a binary go in `local.yml`, which is not.
