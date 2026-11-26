## Summary

<!-- What changed and why? Call out detector, report, CLI, packaging, or workflow changes. -->

## Validation

- [ ] The base of this pull request is `main`, not another branch.
- [ ] Tests were added or updated, or the reason no tests are needed is explained below.
- [ ] Documentation was added or updated where user-facing behavior changed.
- [ ] `pytest -q` passes locally.
- [ ] `ruff check echolot tests action` passes locally.

## LLM assistance

<!--
Required: a pull request without this section filled in may be closed
without a review. CONTRIBUTING.md#llm-assistance says why.

Tick exactly one of the first two boxes. If you tick the second, fill in all
four lines under it. Effort is the reasoning level the tool was set to
(low, high, max, ...), "default" if you did not set one, or "no such setting".
The last box is required either way.
-->

- [ ] No LLM was used for any part of this pull request.
- [ ] An LLM was used:
  - Tool: <!-- Claude Code, Codex, Cursor, GitHub Copilot, ChatGPT, ... -->
  - Model: <!-- the model's name and version -->
  - Effort: <!-- reasoning level -->
  - What the model wrote or changed: <!-- the files or parts: code, tests, docs, this description -->
- [ ] I have read and understand every line of this diff, and I ran the checks under Validation myself.

## Compatibility

- [ ] Existing CLI behavior and report schemas remain compatible.
- [ ] Detector semantics or determinism changes are explicitly described below.
- [ ] Package data was checked if SQL, Claude templates, guides, or other shipped files changed.

## Notes for reviewers

<!-- Include migration notes, known limitations, or follow-up work. -->
