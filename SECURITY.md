# Security Policy

## Supported versions

Security fixes are made against the current `main` branch and the latest
released version of `echolot`. The project currently supports Python 3.10
through 3.14. Older releases and unsupported Python versions may not receive
security fixes.

Releases are published to [PyPI](https://pypi.org/project/echolot/) from
version tags and announced as [GitHub
releases](https://github.com/grishan0v/echolot/releases).

## Reporting a vulnerability

Please report suspected vulnerabilities privately, through the repository's
[Security tab](https://github.com/grishan0v/echolot/security) — **Report a
vulnerability** opens a private thread with the maintainer, and a fix can be
published from that thread as a security advisory. Email
[grishanov.dev@gmail.com](mailto:grishanov.dev@gmail.com) if that route is
inconvenient.

Please include:

- a short description of the vulnerability and its potential impact;
- the affected version, commit, or workflow;
- clear reproduction steps or a minimal proof of concept; and
- any suggested mitigation, if known.

Please do not include secrets, personal data, private traces, or other
sensitive material in the initial report. If those materials are necessary,
describe them first and wait for instructions on a safer way to share them.

## Scope

Reports are in scope when they describe a security issue in the `echolot`
CLI, its published Python package, repository automation, or release and
distribution process.

Out-of-scope reports include ordinary bugs without a security impact, feature
requests, unsupported environments, and vulnerabilities that exist only in
third-party software and cannot be reached through `echolot`. Please report
those through the normal [issue
tracker](https://github.com/grishan0v/echolot/issues).

## Response and disclosure

The maintainer will review private reports and may ask for clarification or
additional reproduction details. There is no guaranteed response or resolution
time. Please do not disclose a vulnerability publicly or open a public issue
until we have had an opportunity to investigate and coordinate a fix.

When appropriate, the maintainer will coordinate disclosure timing with the
reporter, prepare a fix, and publish it through the normal tagged PyPI and
GitHub release process, with an advisory naming the affected versions. Please
avoid including exploit details or sensitive evidence in public issues, pull
requests, or release notes before coordinated disclosure.
