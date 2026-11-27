"""A fake GitHub for the repository settings tests.

It answers with the shapes GitHub's REST API returns for a public repository with classic
branch protection (seen with GET calls; the ids and names are made up).
"""

from __future__ import annotations

import copy
from typing import Any

from devloop.review.github import GITHUB_API, Reply

R = "repos/acme/widgets"
PROTECTION = f"{R}/branches/main/protection"
RULESETS = f"{R}/rulesets?per_page=100"
ACTIONS = f"{R}/actions/permissions"
WORKFLOW = f"{ACTIONS}/workflow"
FORK = f"{ACTIONS}/fork-pr-contributor-approval"
REPORTING = f"{R}/private-vulnerability-reporting"


def on(enabled: bool) -> dict[str, bool]:
    """Return GitHub's ``{"enabled": ...}`` record."""
    return {"enabled": enabled}


def baseline() -> dict[str, Any]:
    """Return every answer for a repository that matches the baseline."""
    checks = [{"context": "check", "app_id": 15368}, {"context": "Merge gate", "app_id": 15368}]
    return {
        R: {
            "full_name": "acme/widgets",
            "default_branch": "main",
            "allow_squash_merge": False,
            "allow_merge_commit": True,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": True,
            "security_and_analysis": {
                "secret_scanning": {"status": "enabled"},
                "secret_scanning_push_protection": {"status": "enabled"},
                "dependabot_security_updates": {"status": "disabled"},
            },
        },
        PROTECTION: {
            "required_status_checks": {
                "strict": False,
                "contexts": ["check", "Merge gate"],
                "checks": checks,
            },
            "required_pull_request_reviews": {
                "dismiss_stale_reviews": False,
                "require_code_owner_reviews": False,
                "required_approving_review_count": 0,
            },
            "enforce_admins": on(True),
            "allow_force_pushes": on(False),
            "allow_deletions": on(False),
            "required_conversation_resolution": on(True),
        },
        RULESETS: [],
        ACTIONS: {"enabled": True, "allowed_actions": "all", "sha_pinning_required": True},
        WORKFLOW: {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": 0},
        FORK: {"approval_policy": "all_external_contributors"},
        REPORTING: on(True),
    }


def ruleset(number: int, *rules: dict[str, Any], **changes: object) -> dict[str, Any]:
    """Return one ruleset as ``GET rulesets/<id>`` answers: active, on the default branch."""
    made = {
        "id": number,
        "name": f"rules-{number}",
        "target": "branch",
        "source_type": "Repository",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "bypass_actors": [],
        "rules": list(rules),
    }
    return {**made, **changes}


def pull_request(**parameters: object) -> dict[str, Any]:
    """Return a ruleset's ``pull_request`` rule."""
    return {"type": "pull_request", "parameters": parameters}


def with_rulesets(answers: dict[str, Any], *rulesets: dict[str, Any]) -> dict[str, Any]:
    """Add ``rulesets`` to the answers: the list, and each ruleset under its id."""
    answers[RULESETS] = [{"id": r["id"], "name": r["name"]} for r in rulesets]
    for one in rulesets:
        answers[f"{R}/rulesets/{one['id']}"] = one
    return answers


def refused(status: int, message: str = "Not Found") -> Reply:
    """Return GitHub's refusal of a call."""
    return Reply(status, {"message": message, "status": str(status)})


NOT_PROTECTED = refused(404, "Branch not protected")


class Fake:
    """A client that answers from a table and records every call it is asked to make."""

    def __init__(self, answers: dict[str, Any]) -> None:
        """Keep the answers, by the path after the API's address."""
        self.answers = answers
        self.calls: list[tuple[str, str, object]] = []

    def __call__(self, method: str, url: str, token: str, body: object) -> Reply:
        """Answer one call; a path with no answer is a 404, as GitHub answers."""
        self.calls.append((method, url, body))
        assert token == "t0ken"  # noqa: S105
        answer = self.answers.get(url.removeprefix(f"{GITHUB_API}/"), refused(404))
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, Reply) else Reply(200, copy.deepcopy(answer))
