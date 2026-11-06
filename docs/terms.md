# Terms of use

[← Docs index](README.md) · [README](../README.md)

echolot is free and open source. The CLI, the plugin for Claude Code, Codex
and Cursor, and the GitHub Action are all released under the
[Apache License 2.0](https://github.com/grishan0v/echolot/blob/main/LICENSE),
and that licence is the whole of the terms: you may use, copy, change and
share echolot under it, at work or anywhere else, without asking.

There is no service behind it to agree to. echolot has no account, no server
and no paid tier; it runs on your machine and sends nothing anywhere.
[Privacy](privacy.md) lists everything it reads, writes and fetches.

## No warranty

echolot comes as it is, without a warranty of any kind, and its authors are
not liable for what follows from using it. Sections 7 and 8 of the licence say
so in full.

## What stays yours

- **Your project and your phone.** `collect` changes some of the phone's
  settings for a set of repeats (airplane mode, the screen's brightness, the
  performance mode) and puts them back when the set ends. `mark --apply` adds
  trace markers to the sources that `instrumentation.allowed` permits, and
  `mark --remove` takes them out. Run it on projects and phones you are
  allowed to change.
- **Your traces and reports.** They stay in the project's `.echolot/` folder,
  and what you do with them is up to you.
- **Your agent.** Claude Code, Codex, Cursor or any other agent reads
  echolot's output and sends it to its model. That is the agent's traffic,
  under its provider's terms.

## What echolot downloads

Two programs, each checked before it is used, as
[Privacy](privacy.md#what-it-fetches) describes: trace_processor from the
Perfetto project, under the Apache License 2.0, and, with
`runner.compose_names` on, the `tracing-perfetto-binary` library from Google's
Maven repository, under the terms published with it.

## Names

Android, Perfetto, Claude, Codex, Cursor and the other products named in these
pages belong to their owners. echolot is an independent project and is not
endorsed by any of them.

## Changes

These terms live in this repository, and every change to them is in its
history. The licence that applies to a release is the one shipped with it.

## Questions

Ask in the [issue tracker](https://github.com/grishan0v/echolot/issues). A
security problem goes through the [security policy](../SECURITY.md) instead.
