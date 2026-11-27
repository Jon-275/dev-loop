# GitHub settings

The repository settings devloop's checks rely on. A rule in a document is not a gate:
the setting is. This page is the baseline for a repository that uses devloop. Each row
names the setting, the value, the reason, and where to set it. The last column also
names the REST API field that shows the live value, where there is one.

In the API paths, `R` is `repos/OWNER/NAME` and `B` is the default branch. The settings
of a branch are under **Settings → Branches** (classic branch protection) or
**Settings → Rules → Rulesets**. Either works. Do not set the same rule in both with
different values.

## The baseline

| Setting | Value | Why | Where it is set (API field) |
|---|---|---|---|
| A pull request before merging | Required, with zero required approvals. | Nothing reaches the default branch without the checks and the reviews, which run on pull requests. Zero approvals, because an author cannot approve their own pull request ([left out on purpose](#left-out-on-purpose)). | Branch rule, "Require a pull request before merging" (`GET R/branches/B/protection`: `required_pull_request_reviews` is present; ruleset rule `pull_request`) |
| Required status checks | Few. Each one reports on every pull request. | A required check that never arrives blocks the merge for ever. A check that is skipped can count as a pass. | Branch rule, "Require status checks to pass" (`GET R/branches/B/protection`: `required_status_checks.checks[].context`) |
| Source of each required check | The app that posts it, not "any source". | Without it, any app or token that can write a status can post the required name. | The same list: pick the app next to each check (`required_status_checks.checks[].app_id`; in a ruleset, `integration_id`) |
| Review status | One: `Merge gate`, in place of one status per reviewer. Required on the default branch only. | One name to require. A pull request of documents only does not wait for a reviewer that never runs. The gate gives no verdict for another base branch ([merge gate](gate.md#rolling-it-out)). | The same list |
| Rules apply to administrators | On | A rule the owner can skip is not a gate. | Branch rule, "Do not allow bypassing the above settings" (`enforce_admins.enabled`; in a ruleset, an empty bypass list, `bypass_actors`) |
| Force pushes to the default branch | Blocked | A force push can remove reviewed commits and the statuses on them. | Branch rule, "Allow force pushes" off (`allow_force_pushes.enabled`; ruleset rule `non_fast_forward`) |
| Deletion of the default branch | Blocked | Deleting the branch removes its protection with it. | Branch rule, "Allow deletions" off (`allow_deletions.enabled`; ruleset rule `deletion`) |
| Conversations resolved before merge | On | Every review finding is a thread. `Review labels` checks each has a labelled reply; this setting checks each is closed. | Branch rule, "Require conversation resolution before merging" (`required_conversation_resolution.enabled`; ruleset `pull_request` rule, `required_review_thread_resolution`) |
| Default workflow token | Read-only. Each job that writes asks for its own permissions. | A workflow that forgets its `permissions` block then holds a token that can change nothing. | Settings → Actions → General → Workflow permissions (`GET R/actions/permissions/workflow`: `default_workflow_permissions` is `read`) |
| Actions pinned to a commit | Every `uses:` names a full commit SHA, and the repository requires it. | A tag can be moved to other code. A commit cannot. | In each workflow file, and Settings → Actions → General → "Require actions to be pinned to a full-length commit SHA" (`GET R/actions/permissions`: `sha_pinning_required`) |
| Fork pull requests and secrets | A pull request from outside reaches no reviewer key until a maintainer has looked at it. A new push withdraws the approval. | The review job holds API keys and a token that writes statuses. | In the caller: `review.yml` with `gate: members` (its default) and `unapprove.yml` ([model reviews](review.md)). No repository setting shows it. |
| Workflow runs from outside contributors | Need approval, for all outside contributors. | A fork's workflow run uses your runners and can post to the pull request. | Settings → Actions → General → "Approval for running fork pull request workflows from contributors" (`GET R/actions/permissions/fork-pr-contributor-approval`: `approval_policy` is `all_external_contributors`) |
| Secret scanning | On | A committed key is reported. | Settings → Advanced Security (`GET R`: `security_and_analysis.secret_scanning.status`) |
| Push protection | On | A push that holds a known kind of key is refused before it is public. devloop sends changed files to the reviewers' vendors and does not redact them ([security model](security.md#what-each-model-vendor-receives)). | Settings → Advanced Security (`GET R`: `security_and_analysis.secret_scanning_push_protection.status`) |
| Dependabot alerts | On | A known vulnerability in a dependency is reported. | Settings → Advanced Security (`GET R/vulnerability-alerts` answers 204 when on) |
| `CODEOWNERS` | Lists `.github/`, `devloop.toml`, and the code that posts a status or holds a key. The file says whether a code owner's review is required or is advice. | A pull request can add a workflow that posts a required status ([security model](security.md#statuses-any-workflow-can-post)). With one maintainer the review cannot be required, because an author cannot approve their own pull request. Say so in the file. | `.github/CODEOWNERS`, and the branch rule "Require review from Code Owners" (`required_pull_request_reviews.require_code_owner_reviews`; ruleset `pull_request` rule, `require_code_owner_review`) |
| `SECURITY.md` and private vulnerability reporting | Both present | A reporter needs a private way in. `SECURITY.md` points to it. | `SECURITY.md` at the root, and Settings → Advanced Security → Private vulnerability reporting (`GET R/private-vulnerability-reporting`: `enabled`) |
| Merge method | Merge commits only | Stacked pull requests need it: after a merge commit the next pull request in the stack needs no rebase. A squash rewrites the commits the next one is built on. | Settings → General → Pull Requests (`GET R`: `allow_merge_commit` true, `allow_squash_merge` and `allow_rebase_merge` false) |
| Delete the branch on merge | On | Merged branches do not pile up, and nobody pushes to one by mistake. In a stack, move the next pull request's base to the default branch before you merge. | Settings → General → "Automatically delete head branches" (`GET R`: `delete_branch_on_merge`) |
| Reviewer API keys | One key per reviewer, used by nothing else, with a spend cap at the provider. | The cap is the limit on what a mistake or a leaked key can cost ([cost model](cost.md)). | The provider's console. GitHub does not show it. |

## Check the settings

`devloop doctor --repo OWNER/NAME` reads the live settings and compares them with the
`[github]` table of `devloop.toml` ([configuration](configuration.md#github)). The
defaults of that table are the baseline above, so a project lists only what it does
differently on purpose. The command only reads: every call is a GET.

```bash
GITHUB_TOKEN=... devloop doctor --repo OWNER/NAME          # one line per setting
GITHUB_TOKEN=... devloop doctor --repo OWNER/NAME --json   # the same, as records
```

Each line is one of:

- `ok`: the setting has the expected value.
- `differs`: it says what was expected and what was found.
- `not judged`: the setting could not be read, with the reason. A token without admin
  rights gets a 403 for branch protection and the Actions settings, and GitHub leaves
  the security and merge fields out of its answer. Some settings do not exist for every
  kind of repository.
- `skipped`: the key is set to `"skip"`.

The exit code is 1 when a setting differs, 3 when none differs but one was not judged,
and 0 only when every setting was judged ok. "Not judged" is never "healthy". Exit code 2
means the command could not start: no token, or a `devloop.toml` that is not valid.

A branch rule counts when classic branch protection or an active ruleset on the default
branch makes it. To say a rule is not there, both must be read. When the two disagree on
code-owner review, the report says so on a line of its own, `code_owner_review_sources`.
`enforce_admins` is ok only when every source that supplies a rule the project expects
holds administrators to it: classic protection with "do not allow bypassing" on, and each
ruleset with an empty bypass list. The rules counted are the required checks, the pull
request, resolved conversations, the blocks on force pushes and deletion, and code-owner
review when it is expected. A ruleset that requires the checks and lets administrators
bypass it differs, whatever classic protection says, and the line names it. A source that
supplies none of those rules does not count. A source that cannot be read leaves the
setting not judged.

These keys are judged: `required_checks`, `required_checks_bound`, `require_pull_request`,
`enforce_admins`, `allow_force_pushes`, `allow_deletions`, `conversation_resolution`,
`code_owner_review`,
`default_token_read_only`, `sha_pinning_required`, `fork_pr_approval`, `secret_scanning`,
`push_protection`, `private_vulnerability_reporting`, `delete_branch_on_merge` and
`merge_methods`. `required_checks` has no default: until the project lists its checks,
that line is `not judged` and shows the checks found.

### Check by hand

The command leaves these out, because the API does not show them reliably:

- **Every required check arrives on every pull request.** GitHub does not say which
  events a check runs on. Read the workflows ([adoption](adoption.md#4-branch-protection)).
- **The merge gate is the one review status.** That is the project's choice of names in
  `required_checks`.
- **Every action is pinned to a commit.** The command checks the repository setting, not
  the workflow files.
- **Fork pull requests cannot reach secrets.** It is in the caller workflow, not a setting.
- **Dependabot alerts.** The API answers 404 both when alerts are off and when the token
  may not see them.
- **`CODEOWNERS` and `SECURITY.md`.** They are files: read them.
- **The spend cap on each reviewer key.** It is at the provider.

## Left out on purpose

These are common advice. A project with one maintainer gains little from them, so the
baseline leaves them out. A team should think again about the first.

- **Required approvals.** An author cannot approve their own pull request. With one
  maintainer, a required approval blocks every pull request or pushes the owner to
  bypass the rules. The gate here is the required statuses and resolved conversations.
- **Signed commits.** A signature shows which key made a commit, not that the change is
  right. Every clone and every coding agent would need a signing key. The review and the
  required statuses do the work.
- **Linear history.** It forbids merge commits, and the stacking rule needs them.
