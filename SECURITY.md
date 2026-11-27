# Security policy

## Reporting a vulnerability

Do not open a public issue for a security problem.

Report it through GitHub's private vulnerability reporting for this repository:
<https://github.com/Jon-275/dev-loop/security/advisories/new>. Only the maintainer sees
the report.

Include the affected command or workflow, the steps to reproduce it, the impact, and a
fix if you have one.

## What to expect

- The maintainer aims to acknowledge a report within five business days.
- A fix is developed in private, with tests. When it is released, an advisory is
  published and credits you, unless you ask not to be named.

There is one maintainer and no on-call rota. There is no commitment to a date for a fix.

## Supported versions

The latest release. Projects pin devloop to a release tag or commit, so a fix reaches a
project when it moves its pin; [upgrading](docs/upgrading.md) says how.

## Scope

In scope: the `devloop` command under `src/`, the shared workflows under
`.github/workflows/`, and the skills and templates devloop writes into a project.
Examples: a pull request that can run code with a project's keys, post a status it
should not, or choose its own review rules.

Out of scope: third-party dependencies and the model vendors' services (report those
upstream), and a project's own changes to the workflows it calls.

The design of the security controls is described in the
[security model](docs/security.md).
