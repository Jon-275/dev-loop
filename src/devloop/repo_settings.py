"""``devloop doctor --repo``: compare a repository's live GitHub settings with ``[github]``.

Read-only: every call is a GET, through the client the other commands use, with its
retries. One line per setting says ``ok``, ``differs`` (expected and found) or
``not judged`` (with the reason, such as a token that may not read the setting).

Invariants:

1. A setting that could not be read is "not judged", never "ok": an answer GitHub
   refused, an answer of another shape, or an expectation the project has not given.
2. A branch rule counts when classic branch protection or an active ruleset on the
   default branch makes it. "Not required" needs both sources read.
3. Exit code 1 when a setting differs; 3 when none differs but one was not judged; 0
   only when every setting was judged ok. A skipped setting is named and decides nothing.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devloop.config import CONFIG_FILE, SKIP, ConfigError, GitHub, load_config
from devloop.github import REPOSITORY
from devloop.repo_read import Facts, Read, Reader, read, value_at
from devloop.review.github import Http, http

OK, DIFFERS, NOT_JUDGED, SKIPPED = "ok", "differs", "not judged", "skipped"
_ADMIN = " (the token needs admin rights to see it)"
_WRITE = " (the token needs write access to see it)"
_METHODS = {
    "merge": "allow_merge_commit",
    "squash": "allow_squash_merge",
    "rebase": "allow_rebase_merge",
}


@dataclass(frozen=True)
class Line:
    """One setting's verdict: its status, what was expected and found, and the reason."""

    setting: str
    status: str
    expected: object = None
    found: object = None
    reason: str = ""

    def text(self) -> str:
        """Return the line as the report prints it."""
        head = f"{self.status:<11}{self.setting}"
        note = f" ({self.reason})" if self.reason else ""
        if self.status == SKIPPED:
            return head
        if self.status == NOT_JUDGED:
            found = "" if self.found is None else f"; found {json.dumps(self.found)}"
            return f"{head}: {self.reason}{found}"
        if self.status == OK:
            return f"{head}: {json.dumps(self.found)}{note}"
        if self.expected is None:
            return f"{head}: {self.reason}"
        return f"{head}: expected {json.dumps(self.expected)}, found {json.dumps(self.found)}{note}"


@dataclass(frozen=True)
class Seen:
    """What GitHub says about one setting: a value, or the reason it is not known."""

    value: object = None
    reason: str = ""


def _value(read: Read, kind: type, *keys: str, hint: str = "") -> Seen:
    """Return the value of type ``kind`` at ``keys`` of an answer, or why it is not known."""
    if read.reason:
        return Seen(reason=read.reason)
    value = value_at(read.data, *keys)
    if isinstance(value, kind):
        return Seen(value)
    return Seen(reason=f"GitHub's answer has no {'.'.join(keys)}{hint}")


def _classic(facts: Facts, *keys: str, absent: bool, missing: bool | None = None) -> Seen:
    """Return a true-or-false of classic branch protection.

    ``absent`` is the answer for a branch with no protection, and ``missing`` the answer
    when a protected branch's record leaves the first key out.
    """
    protection = facts.protection
    if protection.absent:
        return Seen(absent)
    if protection.reason:
        return Seen(reason=f"branch protection: {protection.reason}")
    if missing is not None and value_at(protection.data, keys[0]) is None:
        return Seen(missing)
    return _value(protection, bool, *keys)


def _rules(facts: Facts, kind: str) -> list[Mapping[str, Any]]:
    """Return the parameters of every rule of type ``kind`` in the rulesets read."""
    return [
        params if isinstance(params := rule.get("parameters"), Mapping) else {}
        for ruleset in facts.rulesets
        for rule in ruleset["rules"]
        if isinstance(rule, Mapping) and rule.get("type") == kind
    ]


def _rule(facts: Facts, kind: str, parameter: str = "") -> Seen:
    """Return whether a ruleset has a rule of ``kind`` (with ``parameter`` true, if given)."""
    if facts.rulesets_reason:
        return Seen(reason=facts.rulesets_reason)
    rules = _rules(facts, kind)
    return Seen(any(r.get(parameter) is True for r in rules) if parameter else bool(rules))


def _either(*sources: Seen) -> Seen:
    """Return whether any source requires the rule; "no" needs every source read."""
    if any(source.value is True for source in sources):
        return Seen(True)
    reasons = [source.reason for source in sources if source.reason]
    return Seen(reason="; ".join(reasons)) if reasons else Seen(False)


def _not(seen: Seen) -> Seen:
    """Return the opposite of a true-or-false that was read."""
    return seen if seen.reason else Seen(not seen.value)


def _admins(facts: Facts) -> Seen:
    """Return whether the branch rules bind administrators too."""
    if not facts.protection.absent:
        return _classic(facts, "enforce_admins", "enabled", absent=False)
    if facts.rulesets_reason:
        return Seen(reason=facts.rulesets_reason)
    bypass = [ruleset.get("bypass_actors") for ruleset in facts.rulesets]
    if not all(isinstance(actors, list) for actors in bypass):
        return Seen(reason="a ruleset's bypass list is not shown" + _ADMIN)
    return Seen(bool(bypass) and not any(bypass))


def _checks(facts: Facts) -> Seen:
    """Return the required checks of both sources: each name, and whether an app is bound."""
    if facts.protection.reason or facts.rulesets_reason:
        reason = facts.protection.reason and f"branch protection: {facts.protection.reason}"
        return Seen(reason=reason or facts.rulesets_reason)
    shape = "GitHub's list of required checks has an unexpected shape"
    classic = value_at(facts.protection.data, "required_status_checks")
    lists = [] if classic is None else [(value_at(classic, "checks"), "app_id")]
    for rule in _rules(facts, "required_status_checks"):
        lists.append((rule.get("required_status_checks"), "integration_id"))
    bound: dict[str, bool] = {}
    for listed, key in lists:
        if not isinstance(listed, list):
            return Seen(reason=shape)
        for check in listed:
            name, app = value_at(check, "context"), value_at(check, key)
            if not isinstance(name, str):
                return Seen(reason=shape)
            bound[name] = bound.get(name, False) or (isinstance(app, int) and app > 0)
    return Seen(bound)


def _check_lines(github: GitHub, seen: Seen) -> list[Line]:
    """Judge which checks are required, and that each is bound to the app that posts it."""
    names, want = "required_checks", github.required_checks
    bound_key, want_bound = "required_checks_bound", github.required_checks_bound
    checks: dict[str, bool] = seen.value if isinstance(seen.value, dict) else {}
    found = None if seen.reason else sorted(checks)
    lines = []
    if want == SKIP:
        lines.append(Line(names, SKIPPED))
    elif seen.reason or not want:
        why = seen.reason or "github.required_checks lists no check to expect"
        lines.append(Line(names, NOT_JUDGED, sorted(want), found, why))
    else:
        lines.append(_judge(names, sorted(want), Seen(found)))
    loose = sorted(name for name, bound in checks.items() if not bound)
    if want_bound == SKIP:
        lines.append(Line(bound_key, SKIPPED))
    elif seen.reason:
        lines.append(Line(bound_key, NOT_JUDGED, want_bound, reason=seen.reason))
    elif want_bound and not checks:
        lines.append(Line(bound_key, DIFFERS, True, False, "no status check is required"))
    elif want_bound and loose:
        why = f"any source may post: {', '.join(loose)}"
        lines.append(Line(bound_key, DIFFERS, True, False, why))
    else:
        lines.append(Line(bound_key, OK, want_bound, not loose))
    return lines


def _owner_lines(github: GitHub, facts: Facts) -> list[Line]:
    """Judge code-owner review by either source, and say when the two sources disagree."""
    key, want = "code_owner_review", github.code_owner_review
    if want == SKIP:
        return [Line(key, SKIPPED)]
    path = ("required_pull_request_reviews", "require_code_owner_reviews")
    classic = _classic(facts, *path, absent=False, missing=False)
    ruleset = _rule(facts, "pull_request", "require_code_owner_review")
    seen = _either(classic, ruleset)
    if seen.reason:
        return [Line(key, NOT_JUDGED, want, reason=seen.reason)]
    lines = [Line(key, OK if seen.value == (want is True) else DIFFERS, want, seen.value)]
    both = not (facts.protection.absent or classic.reason) and _rules(facts, "pull_request")
    if both and classic.value != ruleset.value:
        why = (
            f"classic branch protection says {json.dumps(classic.value)} and a ruleset says "
            f"{json.dumps(ruleset.value)}; GitHub applies the stricter one"
        )
        lines.append(Line(f"{key}_sources", DIFFERS, reason=why))
    return lines


def _merge_methods(facts: Facts) -> Seen:
    """Return the merge methods a pull request into the default branch may use."""
    if facts.rulesets_reason:
        return Seen(reason=facts.rulesets_reason)
    allowed = []
    for method, key in _METHODS.items():
        seen = _value(facts.repo, bool, key, hint=_WRITE)
        if seen.reason:
            return seen
        if seen.value:
            allowed.append(method)
    for rule in _rules(facts, "pull_request"):
        narrowed = rule.get("allowed_merge_methods")
        if isinstance(narrowed, list):
            allowed = [method for method in allowed if method in narrowed]
    return Seen(sorted(allowed))


def _judge(setting: str, expected: object, seen: Seen) -> Line:
    """Return the line for one setting: skipped, not judged, ok or differs."""
    if expected == SKIP:
        return Line(setting, SKIPPED)
    if seen.reason:
        return Line(setting, NOT_JUDGED, expected, reason=seen.reason)
    return Line(setting, OK if seen.value == expected else DIFFERS, expected, seen.value)


def _is(seen: Seen, wanted: str) -> Seen:
    """Return whether a text that was read is ``wanted``."""
    return seen if seen.reason else Seen(seen.value == wanted)


def check(github: GitHub, facts: Facts) -> list[Line]:
    """Return one line per setting of ``[github]``, in the order of the baseline page."""
    force = _either(
        _not(_classic(facts, "allow_force_pushes", "enabled", absent=True)),
        _rule(facts, "non_fast_forward"),
    )
    deletion = _either(
        _not(_classic(facts, "allow_deletions", "enabled", absent=True)),
        _rule(facts, "deletion"),
    )
    resolved = _either(
        _classic(facts, "required_conversation_resolution", "enabled", absent=False),
        _rule(facts, "pull_request", "required_review_thread_resolution"),
    )
    security = ("security_and_analysis", "secret_scanning", "status")
    protection = ("security_and_analysis", "secret_scanning_push_protection", "status")
    methods = github.merge_methods
    simple = [
        ("enforce_admins", _admins(facts)),
        ("allow_force_pushes", _not(force)),
        ("allow_deletions", _not(deletion)),
        ("conversation_resolution", resolved),
    ]
    rest = [
        (
            "default_token_read_only",
            _is(_value(facts.workflow, str, "default_workflow_permissions"), "read"),
        ),
        ("sha_pinning_required", _value(facts.actions, bool, "sha_pinning_required")),
        ("fork_pr_approval", _value(facts.fork_approval, str, "approval_policy")),
        ("secret_scanning", _is(_value(facts.repo, str, *security, hint=_ADMIN), "enabled")),
        ("push_protection", _is(_value(facts.repo, str, *protection, hint=_ADMIN), "enabled")),
        ("private_vulnerability_reporting", _value(facts.reporting, bool, "enabled")),
        ("delete_branch_on_merge", _value(facts.repo, bool, "delete_branch_on_merge", hint=_WRITE)),
    ]
    return [
        *_check_lines(github, _checks(facts)),
        *(_judge(key, getattr(github, key), seen) for key, seen in simple),
        *_owner_lines(github, facts),
        *(_judge(key, getattr(github, key), seen) for key, seen in rest),
        _judge(
            "merge_methods",
            methods if methods == SKIP else sorted(methods),
            _merge_methods(facts),
        ),
    ]


def exit_code(lines: list[Line]) -> int:
    """Return 1 when a setting differs, 3 when one was not judged, else 0."""
    statuses = {line.status for line in lines}
    return 1 if DIFFERS in statuses else 3 if NOT_JUDGED in statuses else 0


def main(
    root: Path,
    repo: str,
    as_json: bool,
    env: Mapping[str, str],
    send: Http = http,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Print the report for ``repo``; 2 when the command cannot start."""
    try:
        github = load_config(root / CONFIG_FILE).github
    except ConfigError as error:
        print(f"devloop doctor: {error}", file=sys.stderr)
        return 2
    token = env.get("GITHUB_TOKEN", "")
    if not token or not REPOSITORY.fullmatch(repo):
        print("devloop doctor: GITHUB_TOKEN and --repo owner/name are required", file=sys.stderr)
        return 2
    lines = check(github, read(Reader(token, send, sleep), repo))
    if as_json:
        records = [
            {
                "setting": line.setting,
                "status": line.status,
                "expected": line.expected,
                "found": line.found,
                "reason": line.reason,
            }
            for line in lines
        ]
        print(json.dumps(records, indent=2))
    else:
        counts = {s: sum(line.status == s for line in lines) for s in (OK, DIFFERS, NOT_JUDGED)}
        skipped = sum(line.status == SKIPPED for line in lines)
        print("\n".join(line.text() for line in lines))
        print(
            f"{repo}: {counts[OK]} ok, {counts[DIFFERS]} differ, "
            f"{counts[NOT_JUDGED]} not judged, {skipped} skipped"
        )
    return exit_code(lines)
