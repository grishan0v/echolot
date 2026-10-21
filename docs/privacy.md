# Privacy

[← Docs index](README.md) · [README](../README.md)

echolot runs on your machine and keeps what it makes there. It has no
account, no server of its own and no telemetry, and it sends nothing
anywhere: not the traces, not the reports, not a count of how often it ran.
The plugin for Claude Code, Codex and Cursor is text: four skills that tell
the agent which `echolot` command to run next.

This page is everything the CLI reads, writes and fetches. The network
appears in two places, both downloads, and each is checked before it is used.

## What it reads

- **The project.** `echolot.yml`, and the build files and sources that
  `setup` scans and `analyze` points a finding at.
- **The phone**, through `adb`: the traces `collect` records of the app, and
  the state a set of repeats depends on, such as whether the screen is on
  and what a temperature sensor reads. With `runner.compose_names` on, it
  also asks the app which version of the tracing library it carries, and the
  phone which CPU it has.
- **The agent's own session files**, for `echolot-reflect` (`echolot
  reflect`) only, read where they are on this machine: Claude Code's under
  `~/.claude/projects/`, Codex's under `~/.codex/sessions/`, and echolot's
  own run log, `.echolot/log/runs.jsonl`. They are read locally and nothing
  from them leaves the machine; what reflect found goes to
  `.echolot/reflect/`.
- **The agents' settings**, so that `echolot`, `status` and `doctor` can say
  whether the plugin and Codex's rule are in place:
  `~/.claude/plugins/installed_plugins.json` and `~/.claude/settings.json`,
  the copy in `~/.cursor/plugins/local/echolot`, and `~/.codex/config.toml`
  and `~/.codex/rules/`.
- **Gradle's cache**, with `runner.compose_names` on, for the
  `tracing-perfetto-binary` library a project that built once already has.

## What it writes

In the project:

- `echolot.yml`, which `setup` writes;
- `.echolot/`: traces, reports, what reflect found, and the run log. The log
  holds one line per command: which command, when, for how long, its exit
  code and a few facts it noted. `ECHOLOT_NO_RECORD=1` switches it off;
- a few `.gitignore` lines, or with `init --private` the repository's own
  `.git/info/exclude`;
- what `echolot init` installs for the agents you choose: the `.claude/`
  layer, a block between `<!-- echolot -->` markers in `AGENTS.md`,
  `GEMINI.md` or `.github/copilot-instructions.md`,
  `.cursor/rules/echolot.mdc`, and in Codex `.codex/rules/echolot.rules`,
  which lets `echolot` out of the sandbox;
- while an investigation needs them, temporary trace markers in the sources
  that `instrumentation.allowed` permits. `echolot mark --remove` takes them
  out.

Outside the project:

- `~/.local/share/perfetto/prebuilts/`, where trace_processor is kept once it
  is downloaded, and an empty lock file in the system's temporary directory
  that keeps two downloads of it from running at once;
- `~/.cache/echolot/` (or `$XDG_CACHE_HOME/echolot`), with
  `runner.compose_names` on: the tracing library;
- `~/.cursor/plugins/local/echolot`, only when you run `echolot plugin
  cursor`.

On the phone, `collect` records to one file,
`/data/misc/perfetto-traces/echolot.pftrace`, and pulls it into the project;
the next repeat replaces it. What the `runner:` keys change for a set,
airplane mode, the screen's brightness or the performance mode, is put back
when the set ends. With `runner.compose_names` on, `collect` pushes the
tracing library into the app's own media directory and removes it after the
set.

## What it fetches

Two downloads, each once per version:

- **trace_processor**, the program that reads a trace. The build the
  `perfetto` Python package pins, from Perfetto's artifact storage
  (`commondatastorage.googleapis.com/perfetto-luci-artifacts`), checked
  against the SHA-256 in that package's manifest. 10 to 14 MB, the first
  time a command needs it. `--tp-binary`, or `toolchain.tp_binary` in
  `local.yml`, names a binary of your own instead, and then nothing is
  downloaded. [The download](determinism.md#the-download) says more.
- **tracing-perfetto-binary**, only with `runner.compose_names` on: the
  library that switches on the app's tracing, in the version the app asks
  for. From Gradle's cache when it is there; otherwise from Google's Maven
  (`dl.google.com/android/maven2`), checked against the SHA-1 published beside
  it. The app checks it again against the sums its own version carries.

Installing is separate and yours: `pipx install echolot` fetches the package
from PyPI, Claude Code and Codex fetch the plugin from
[this repository](https://github.com/grishan0v/echolot) at a release tag, and
`echolot plugin cursor` copies it for Cursor from the installed package.

## What it sends

Nothing. There is no telemetry, no crash report and no update check.

Two connections stay on the machine: perfetto starts trace_processor and
talks to it over HTTP on localhost, and `adb` talks to its server on
localhost:5037, which reaches the phone over USB or your local network.

The agent you run reads echolot's output, and sends what it reads to its
model as part of the session. That traffic is the agent's, under its
provider's terms.

## Questions

Ask in the [issue tracker](https://github.com/grishan0v/echolot/issues). A
security problem goes through the [security policy](../SECURITY.md) instead.
