"""The council's report and its verdict: the ``Council review`` status's single source.

A council review (the ``council-review`` skill) ends in a report, ``review-pr<N>.md``,
whose frontmatter records what was judged::

    ---
    pr: 12
    head: "<the 40-character commit the council reviewed>"
    tier: 1
    reviewers: ["core", "security"]
    verdict: "safe"
    findings: {"P0": 0, "P1": 0, "P2": 2, "P3": 1}
    ---

:func:`judge` turns a report into a verdict for a pull request's current head. Only a
report on that head, at the change's tier or a riskier one, from every reviewer the
change summons, saying safe with no P0 or P1, is a success. Anything not judged (no
report, another commit, a lower tier, a missing reviewer) is pending, never a pass
(decision 0003). A tier outside ``council.require_status`` needs no council: success.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeGuard

from devloop.council import Plan

KEYS = ("pr", "head", "tier", "reviewers", "verdict", "findings")
SEVERITIES = ("P0", "P1", "P2", "P3")
VERDICTS = ("safe", "not safe")
_SHA = re.compile(r"^[0-9a-f]{40}$")
#: GitHub keeps at most this many characters of a status description.
DESCRIPTION_LIMIT = 140


class ReportError(ValueError):
    """Raised when a council report cannot be read or breaks the report format."""


@dataclass(frozen=True)
class Report:
    """What a council report says it judged, and its verdict."""

    pr: int
    head: str
    tier: int
    reviewers: tuple[str, ...]
    verdict: str
    findings: dict[str, int]


@dataclass(frozen=True)
class Verdict:
    """The Council review status for one head: success, failure or pending, and why."""

    state: str
    description: str


def _fields(text: str) -> dict[str, object]:
    """Return the report's frontmatter, each value parsed as JSON."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ReportError("a council report must start with --- frontmatter")
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        raise ReportError("the report's frontmatter is never closed with ---") from None
    fields: dict[str, object] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key not in KEYS:
            raise ReportError(f"{key} is not a report key ({', '.join(KEYS)})")
        if key in fields:
            raise ReportError(f"{key} appears twice in the report")
        try:
            fields[key] = json.loads(value.strip())
        except ValueError:
            raise ReportError(f"{key}: {value.strip()} is not valid JSON") from None
    for key in KEYS:
        if key not in fields:
            raise ReportError(f"{key} is missing from the report")
    return fields


def _count(value: object) -> TypeGuard[int]:
    """Return whether ``value`` is a whole, non-negative count."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def parse_report(text: str) -> Report:
    """Parse and validate the council report in ``text``."""
    fields = _fields(text)
    pr, head, tier = fields["pr"], fields["head"], fields["tier"]
    reviewers, verdict, findings = fields["reviewers"], fields["verdict"], fields["findings"]
    if not _count(pr) or pr == 0:
        raise ReportError("pr must be the pull request's number")
    if not isinstance(head, str) or not _SHA.match(head):
        raise ReportError("head must be the full 40-character commit the council reviewed")
    if not _count(tier) or tier not in (1, 2, 3):
        raise ReportError("tier must be 1, 2 or 3")
    if not isinstance(reviewers, list) or not all(isinstance(r, str) and r for r in reviewers):
        raise ReportError("reviewers must be a list of reviewer names")
    if not isinstance(verdict, str) or verdict not in VERDICTS:
        raise ReportError(f"verdict must be one of {', '.join(VERDICTS)}")
    if not isinstance(findings, dict) or set(findings) != set(SEVERITIES):
        raise ReportError(f"findings must give a count for each of {', '.join(SEVERITIES)}")
    if not all(_count(n) for n in findings.values()):
        raise ReportError("findings must be whole, non-negative counts")
    counts = {severity: int(findings[severity]) for severity in SEVERITIES}
    return Report(pr, head, tier, tuple(reviewers), verdict, counts)


def _limit(text: str) -> str:
    """Return ``text`` cut to fit a status description."""
    return text if len(text) <= DESCRIPTION_LIMIT else text[: DESCRIPTION_LIMIT - 1] + "…"


def judge(
    report: Report | None,
    head: str,
    plan: Plan,
    required: Sequence[int],
    pr: int | None = None,
) -> Verdict:
    """Return the Council review verdict for ``head``, whose change ``plan`` describes.

    ``pr``, when known, is the pull request being judged: a report for another one is
    pending, since one pull request's review never stands for another's.
    """
    if plan.tier not in required:
        return Verdict("success", f"tier {plan.tier}: council review not required")
    if report is None:
        return Verdict("pending", f"no council report for {head[:7]} (tier {plan.tier})")
    if pr is not None and report.pr != pr:
        return Verdict("pending", f"the report is for #{report.pr}, not #{pr}")
    if report.head != head:
        return Verdict(
            "pending",
            f"the report judged {report.head[:7]}, not the head {head[:7]}: run a delta check",
        )
    if report.tier > plan.tier:
        return Verdict(
            "pending", f"reviewed as tier {report.tier}, but the change is tier {plan.tier}"
        )
    missing = sorted(set(plan.reviewers) - set(report.reviewers))
    if missing:
        return Verdict("pending", _limit(f"not reviewed by: {', '.join(missing)}"))
    counts = ", ".join(f"{s} {report.findings[s]}" for s in SEVERITIES)
    if report.verdict != "safe" or report.findings["P0"] or report.findings["P1"]:
        return Verdict("failure", _limit(f"not safe: {counts}"))
    return Verdict("success", _limit(f"safe: {counts}"))
