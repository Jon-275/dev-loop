"""Whether a branch's rules hold administrators too, for ``devloop doctor --repo``.

Two kinds of source make branch rules: classic branch protection, and each ruleset on
the branch. Each kind has its own switch for administrators: "do not allow bypassing"
on classic protection, and the bypass list of a ruleset.

Invariant: administrators are held to the rules only when every source that supplies a
protection the project expects holds them to it. A ruleset that requires the checks but
lets administrators bypass it is not made good by classic protection that binds them
and requires nothing. A source that supplies nothing expected does not count, and a
source that cannot be read leaves the answer unknown.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from devloop.config import SKIP, GitHub
from devloop.repo_read import Facts, Seen, classic, either, opposite, rule, rules, value_at

CLASSIC = "classic branch protection"


def _expected(github: GitHub) -> dict[str, bool]:
    """Return which protections the project expects a source to supply."""
    return {
        "checks": github.required_checks_bound is True or github.required_checks not in (SKIP, ()),
        "pull_request": github.require_pull_request is True,
        "conversations": github.conversation_resolution is True,
        "no_force_push": github.allow_force_pushes is False,
        "no_deletion": github.allow_deletions is False,
        "code_owner": github.code_owner_review is True,
    }


def _from_classic(facts: Facts) -> dict[str, Seen]:
    """Return which protections classic branch protection supplies."""
    data = facts.protection.data
    reviews = ("required_pull_request_reviews", "require_code_owner_reviews")
    return {
        "checks": Seen(bool(value_at(data, "required_status_checks", "checks"))),
        "pull_request": Seen(isinstance(value_at(data, reviews[0]), Mapping)),
        "conversations": classic(
            facts, "required_conversation_resolution", "enabled", absent=False
        ),
        "no_force_push": opposite(classic(facts, "allow_force_pushes", "enabled", absent=True)),
        "no_deletion": opposite(classic(facts, "allow_deletions", "enabled", absent=True)),
        "code_owner": classic(facts, *reviews, absent=False, missing=False),
    }


def _from_ruleset(one: Facts) -> dict[str, Seen]:
    """Return which protections the one ruleset in ``one`` supplies."""
    listed = [
        params.get("required_status_checks") for params in rules(one, "required_status_checks")
    ]
    return {
        "checks": Seen(any(listed)),
        "pull_request": rule(one, "pull_request"),
        "conversations": rule(one, "pull_request", "required_review_thread_resolution"),
        "no_force_push": rule(one, "non_fast_forward"),
        "no_deletion": rule(one, "deletion"),
        "code_owner": rule(one, "pull_request", "require_code_owner_review"),
    }


def _sources(facts: Facts) -> list[tuple[str, dict[str, Seen], Seen]]:
    """Return each source: its name, what it supplies, and whether it holds administrators."""
    found = []
    if not facts.protection.absent:
        holds = classic(facts, "enforce_admins", "enabled", absent=False)
        found.append((CLASSIC, _from_classic(facts), holds))
    for ruleset in facts.rulesets:
        bypass = ruleset.get("bypass_actors")
        holds = (
            Seen(not bypass)
            if isinstance(bypass, list)
            else Seen(reason="a ruleset's bypass list is not shown (the token needs admin rights)")
        )
        name = f"ruleset {ruleset.get('name')!r}"
        found.append((name, _from_ruleset(replace(facts, rulesets=(ruleset,))), holds))
    return found


def admins(github: GitHub, facts: Facts) -> tuple[Seen, str]:
    """Return whether administrators are held to every expected rule, and a note that says why.

    The note names each source that supplies an expected protection and lets
    administrators bypass it, or says that no source supplies one.
    """
    if facts.protection.reason or facts.rulesets_reason:
        classic_reason = facts.protection.reason and f"branch protection: {facts.protection.reason}"
        return Seen(reason=classic_reason or facts.rulesets_reason), ""
    expected = _expected(github)
    supplying, loose = 0, []
    for name, supplies, holds in _sources(facts):
        supplied = either(*(seen for key, seen in supplies.items() if expected[key]))
        if supplied.reason:
            return supplied, ""
        if not supplied.value:
            continue
        if holds.reason:
            return holds, ""
        supplying += 1
        if not holds.value:
            loose.append(name)
    if loose:
        return Seen(False), f"administrators may bypass {', '.join(loose)}"
    if not supplying:
        return Seen(False), "no source supplies a rule the project expects"
    return Seen(True), ""
