# echolot plugin

Skills for Claude Code, Codex and Cursor that find the cause of an Android performance
regression: a slow cold start, a stuttering scroll, an ANR. The agent records
Perfetto traces of the app, reads the report the
[echolot](https://github.com/grishan0v/echolot) CLI builds from them, and
follows a finding down to a file and a line. It never reads the trace itself.

## The skills

- `echolot`: the way in. Asks the CLI where the project stands and takes the
  next step.
- `echolot-setup`: writes `echolot.yml` from a scan of the repository, a probe
  trace and a few questions only a person can answer.
- `echolot-hunt`: one investigation, from the question to the place in the
  code, with the loop run by a subagent that starts clean.
- `echolot-reflect`: for whoever maintains echolot. Looks back at the last
  session that used it and proposes changes to the tool. Nothing is edited
  without asking.

## What it needs

The plugin is text only; the CLI it drives is installed separately:

```bash
pipx install echolot
```

Also `adb` on `PATH` and an Android phone or emulator with USB debugging on.

## What it runs, writes, fetches and sends

- **Runs** the `echolot` CLI. The CLI runs `adb` to launch and trace the app,
  and, when `echolot.yml` says so, the project's own Gradle task.
- **Reads**, for `echolot-reflect` only, the agent's own session files on
  this machine, and writes what it found under `.echolot/reflect/`.
- **Writes** in the project: `echolot.yml`, traces and reports under
  `.echolot/`, a few `.gitignore` lines, and in Codex
  `.codex/rules/echolot.rules`, which lets `echolot` out of the sandbox.
  While an investigation needs them, it adds temporary trace markers to the
  sources that `instrumentation.allowed` permits, and `echolot mark --remove`
  takes them out.
- **Fetches**, once: `trace_processor` from Perfetto's artifact storage,
  checked against the SHA-256 the `perfetto` package pins, 10 to 14 MB. With
  `runner.compose_names` on, the `tracing-perfetto-binary` library from
  Gradle's cache or Google's Maven, checked against its SHA-1.
- **Sends** nothing anywhere.

Documentation: [grishan0v.github.io/echolot](https://grishan0v.github.io/echolot/).
License: Apache 2.0.
