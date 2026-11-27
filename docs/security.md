# Security model

## Trusted reviews on `pull_request_target`

A model review needs an API key and a token that can post comments and statuses. A
`pull_request` workflow from a fork gets neither, so the review runs on
`pull_request_target`, which runs the **base branch's** copy of the workflow with secrets.
That is safe only if nothing the pull request controls is executed:

- devloop is installed from a pinned release (or, in dev-loop itself, from the base
  branch's checkout), never from the pull request.
- `devloop.toml`, the lenses, the feedback store and the reviewer endpoints are read from
  the base branch's checkout. A pull request cannot point a key at another host.
- The pull request's commit is fetched apart and only read through git, with its
  `.gitattributes`, diff drivers and text conversion disabled. Nothing in it is installed,
  imported or run, and no cache is shared with it.

The `Review labels` reply workflow runs on review events with no secrets and the default
token, using the base branch's devloop.

## What each model vendor receives

For each pull request a reviewer reviews, its vendor receives:

- the reviewer's lens, the shared finding rules, and the feedback examples;
- the diff of the files matching `review.files` (up to 120,000 characters);
- the full text of up to 15 changed files (up to 250,000 characters in all);
- the names of changed files the diff leaves out.

devloop does not redact secrets from that text. A secret committed in a pull request is
sent to every reviewer's vendor, so turn on secret scanning with push protection, and
leave secret-bearing files out of `review.files`.

## Statuses any workflow can post

Any workflow in a same-repository pull request can post any commit status, including a
required one such as `Gemini review`. A required status is only as trustworthy as the
workflows that can run. Protect `.github/workflows/` with a `CODEOWNERS` entry and
require code-owner review, so a pull request cannot add a workflow that posts a fake pass.

## A status belongs to a commit

GitHub keeps a commit status on the commit, not on the pull request. A pull request from
a fork can have, as its head, a commit that is also the head of a branch in your
repository: anyone can point a fork's branch at that commit and open a pull request. A
status written for the fork's pull request would land on the shared commit and replace
the one the branch's own pull request earned. `Review labels` is the plain case: the
fork's pull request has no finding threads, so its pass would replace a failure.

The rule: for a fork's pull request whose head is the head of at least one branch in the
base repository, `labels.yml`, `review.yml` and `council.yml` (and
`devloop council report --post`) write no status on that commit, pending or verdict.
The run says why in a notice and succeeds, and the review does not run, so no key is
used. A deleted fork counts as a fork. A pull request from the repository itself is
never asked about, and behaves as before. When the check itself fails the run fails; it
may still post a failure, which can never grant a pass.

The limits:

- Such a fork pull request gets no status of its own, so it cannot merge where those
  statuses are required. Push a new commit to it, or open it from a branch in the
  repository.
- The branches are read when the run starts. A branch pushed to the fork's head
  afterwards, for example when a maintainer pushes a contributor's commit to a branch
  unchanged, shares the statuses the fork's pull request already has on that commit.
  Check them before relying on them, or add a commit.
- The guard covers devloop's workflows only. Any other workflow that posts a status on
  `pull_request_target` or review events needs the same check.

## Skills supply chain

Skills are instructions an agent follows with the user's permissions. devloop renders only
its own skills and the project's, from files in the repository, and every rendered copy is
checked by `devloop doctor --check`. Never install a community skill without reading it:
malicious skills have been found in public registries.

## Hooks

The git hooks devloop installs (`pre-push`, `commit-msg`) never replace another tool's
hook, and the `commit-msg` hook never blocks a commit. They are fast feedback, not the
gate: CI is.
