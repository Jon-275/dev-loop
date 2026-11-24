# The council

The council is a set of roles that review tier-1 and tier-2 changes with proof: every P0
and P1 finding comes with a command that was run. Each project designs its own.

## Writing a role card

One Markdown file per role in `council.roles_dir` (default `devloop/council/`):

```markdown
---
name: payments
title: Payments Engineer
summoned_by: ["src/billing/**"]
tiers: [1, 2]
reviewer: money
---
Mandate: money is never lost, doubled or rounded wrong.
Checks: a double charge on retry; rounding before summing; floats for money.
Severity anchors: P0 = a customer charged twice; P1 = a rounding error in a total.
Proof methods: replay charges with injected timeouts; sum the ledger.
Decisions enforced: docs/decisions/0001-money.md
Out of scope: pricing decisions.
Known false positives: "missing retry" where the queue already retries.
```

- `name` matches the file name; `summoned_by` globs and `tiers` say when the role is
  called; roles that share a `reviewer` run in one pass.
- `Mandate`, `Checks`, `Severity anchors` and `Proof methods` are required. An indented
  line continues the field above it.
- `Out of scope` and `Known false positives` come from the role's rejected findings, and
  cut repeat noise.

`devloop council plan` shows which roles and reviewers a change summons.

## The role library

`devloop council library` lists the roles devloop ships; `devloop council add <role>`
copies one into the project to specialise.

- **Always present:** architect, test-quality, security, operability, claims-docs (every
  claim in docs and reports is true and reproducible) and editor (the council's own report
  is plain and proven).
- **Domain packs:** ml-statistics, data-engineering, crm-revops, gtm-strategy, payments,
  regulated-data, frontend-ux, mobile, infrastructure, llm-apps, public-api.

Together they fit the default reviewer budget: five reviewers for tier 1 (core,
security, operations, claims, domain) and two for tier 2 (core, security).

## Reporting the result

The `council-review` skill writes its report to `review-pr<N>.md` in the notes folder,
starting with frontmatter that records what was judged: the pull request, the full
`head` commit reviewed, the `tier`, the `reviewers` that ran, the `verdict` (`"safe"` or
`"not safe"`) and the `findings` per severity. `devloop council report --report <file>`
judges it for the checkout's head:

| The report | Council review |
|---|---|
| The change's tier is not in `council.require_status` | success: not required |
| Missing, for another commit, at a lower tier, or missing a summoned reviewer | pending |
| `not safe`, or any P0 or P1 | failure |
| Safe, no P0 or P1, every reviewer, this head, this tier or riskier | success |

Pending is never a pass: a report on an older commit judged that commit, so a new push
needs a delta check and a new report. A report that cannot be read is an error (exit 2).

## The Council review status

`devloop council report --post --pr <N> --report <file>` posts the verdict above as the
`Council review` commit status on the pull request's head, read from GitHub with
`GITHUB_TOKEN` (or `gh auth token`). Without `--report`, it posts what CI knows: pending
while the head's tier needs a council, success where none is required, and it never
overwrites a council's verdict already posted on that exact head (a verdict posted
while CI is running can be set back to pending; post it again). Exit 0 is success, 1
pending or failure (posted, or a verdict kept), 2 the command could not run: missing
settings, an unreadable report, or a failed GitHub read, and then nothing is posted. A
renamed file counts at both its old and new path, so moving code never lowers a tier.

Call the shared `council.yml` on every push to a pull request, so a new head never keeps
an old verdict, then make `Council review` a required check:

```yaml
on:
  pull_request_target: { types: [opened, synchronize, reopened, ready_for_review] }
jobs:
  council:
    uses: Jon-275/dev-loop/.github/workflows/council.yml@<tag>
    permissions: { contents: read, pull-requests: read, statuses: write }
    with: { devloop: "git+https://github.com/Jon-275/dev-loop@<tag>" }
```

The status is an attestation: anyone who can write statuses can post one, so require it
alongside branch protection that limits who can push.

## Drift warnings

`devloop doctor` and `devloop council check` print warnings, which never fail a check,
when the council may no longer fit the project:

- **A signal no role covers:** a dependency (in `pyproject.toml`, `requirements*.txt` or
  `package.json`, at the root or one folder down) or a file that calls for a library
  pack the council has no card for, such as `stripe` for payments or `*.tf` for
  infrastructure. Add the pack (`devloop council add <pack>`) or, if the owner decided
  against it, list it in `council.declined`. A pack's card keeps the pack's name
  (`payments.md`) when it is specialised, so the signal counts as covered.
- **A decision no role enforces:** a file in `docs/decisions/` that no card names under
  "Decisions enforced". Name it in a card if a review should check it.

A project without role cards gets no warnings.

## Designing and proving a council

Run the `council-design` skill. It scans the project for signals, interviews the owner,
picks and specialises cards, groups them within `council.max_reviewers`, and records the
council as a decision record (`docs/decisions/NNNN-council.md`).

A new council is trusted only once it catches known defects: run it on past pull requests
before their fixes and measure recall (known defects found) and precision (findings that
are real). The owner sets the bar, for example 70% of replayed P0 and P1 defects.

## Keeping it current

`devloop doctor` fails when a card is invalid, a `summoned_by` glob matches no file, a
named decision file does not exist, or a tier has more reviewers than its budget.

Review the council each quarter. Add a role where accepted findings cluster in an area no
role covers; merge or retire a role with almost no unique accepted findings; and re-run
the proof after any change, in a PR that updates the council's decision record.
