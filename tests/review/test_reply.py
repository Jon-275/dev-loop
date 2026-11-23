"""Reading a reviewer's reply, and the summary concerns it posts nowhere (gtm_toolkit PR #229)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from review_helpers import PROVIDER

from devloop.review import reply
from devloop.review.model import Finding, NotPosted, ReviewError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "review_replies"


def _reply(
    *findings: dict[str, Any], summary: str = "Adds a sync.", not_posted: object = None
) -> str:
    """Return a reply in the expected JSON shape."""
    return json.dumps(
        {"summary": summary, "findings": list(findings), "not_posted": not_posted or []}
    )


def _finding(path: str = "app.py", title: str = "Bug", severity: str = "P2") -> dict[str, Any]:
    """Return one finding in the reply's JSON shape."""
    return {"severity": severity, "path": path, "line": 2, "title": title, "body": "Fix it."}


def test_a_reply_is_parsed_and_sorted_by_severity() -> None:
    review = reply.parse_review(
        PROVIDER, _reply(_finding(severity="P3"), _finding("./b.py", severity="P1"))
    )
    assert [(f.severity, f.path) for f in review.findings] == [("P1", "b.py"), ("P3", "app.py")]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        json.dumps({"summary": 1, "findings": []}),
        _reply({**_finding(), "severity": "P0"}),
        _reply({**_finding(), "path": ""}),
        _reply({**_finding(), "line": True}),
        _reply({**_finding(), "title": None}),
    ],
)
def test_a_reply_that_is_not_the_expected_json_fails_the_check(text: str) -> None:
    with pytest.raises(ReviewError):
        reply.parse_review(PROVIDER, text)


@pytest.mark.parametrize(
    "value",
    [
        "nope",
        [1],
        [{"title": "t"}],
        [{"title": " ", "reason": "r"}],
        [{"title": "t", "reason": " "}],
    ],
)
def test_a_malformed_not_posted_list_fails_the_check(value: object) -> None:
    with pytest.raises(ReviewError, match="not_posted"):
        reply.parse_review(PROVIDER, _reply(not_posted=value))


def test_dimensions_are_required_when_the_provider_has_them() -> None:
    from dataclasses import replace

    tagged = replace(PROVIDER, dimensions={"D1": "Revenue alignment"})
    review = reply.parse_review(tagged, _reply({**_finding(), "dimension": "D1"}))
    assert review.findings[0].dimension == "D1"
    with pytest.raises(ReviewError, match="dimension"):
        reply.parse_review(tagged, _reply({**_finding(), "dimension": "D9"}))


def test_the_pr229_reply_lists_its_summary_concern_instead_of_no_findings() -> None:
    recorded = json.loads((FIXTURES / "reflection_pr229_0c8a6ccc.json").read_text())["reply"]
    review = reply.parse_review(PROVIDER, json.dumps(recorded))
    assert review.findings == ()
    [concern] = review.not_posted
    assert concern.title.startswith("The main enterprise-readiness concerns are around")
    assert concern.reason == reply.SUMMARY_ONLY_REASON


def test_a_summary_concern_beside_findings_that_matches_none_is_listed() -> None:
    review = reply.parse_review(
        PROVIDER,
        _reply(
            _finding(title="Stale CRM field left behind"),
            summary=(
                "The stale CRM field left behind could mislead reps. Separately, the migration "
                "could drop persisted rows."
            ),
        ),
    )
    assert review.not_posted == (
        NotPosted(
            "Separately, the migration could drop persisted rows.",
            reply.SUMMARY_UNMATCHED_REASON,
        ),
    )


def test_a_summary_concern_beside_listed_concerns_is_listed_once() -> None:
    review = reply.parse_review(
        PROVIDER,
        _reply(
            summary="Persisted rows may not load after upgrade. The CRM clear could misreport.",
            not_posted=[{"title": "Persisted rows may not load", "reason": "no single line"}],
        ),
    )
    assert [c.title for c in review.not_posted] == [
        "Persisted rows may not load",
        "The CRM clear could misreport.",
    ]


def test_a_summary_that_only_describes_the_change_has_no_concern() -> None:
    summary = "Adds withheld churn scoring and clears the CRM risk level."
    assert reply.parse_review(PROVIDER, _reply(summary=summary)).not_posted == ()


@pytest.mark.parametrize(
    "sentence",
    [
        "The sync crashes when the CRM returns 429.",
        "Retries fail after the first timeout.",
        "The merge loses the CRM id.",
        "A second run drops rows silently.",
        "The token leaks into the log.",
        "The cache serves stale scores.",
    ],
)
def test_a_plain_failure_statement_in_the_summary_is_listed(sentence: str) -> None:
    assert reply.summary_concerns(f"Adds a sync. {sentence}") == [sentence]


def test_a_reworded_mention_of_a_finding_is_still_listed_for_a_check_by_hand() -> None:
    sentence = (
        "One CI workflow defect was found where a pipe without `pipefail` can mask script failures."
    )
    review = reply.parse_review(
        PROVIDER,
        _reply(
            _finding(
                ".github/workflows/loop-metrics.yml",
                "Pipe to tee masks non-zero exit codes from loop_metrics.py",
            ),
            summary=f"This PR adds a weekly workflow. {sentence}",
        ),
    )
    assert review.not_posted == (NotPosted(sentence, reply.SUMMARY_UNMATCHED_REASON),)


def test_titles_compare_by_their_content_words() -> None:
    assert reply.normalise_title("  Retry\u200b  FAILED writes ") == "retry failed writes"
    assert reply.title_similarity("Retry failed writes", "Retrying failed writes") == 1.0
    assert reply.title_similarity("Retry writes", "Log reads") == 0.0
    assert Finding("P1", "a.py", 1, "t", "b").dimension is None


#: nothing was found, or describes what the change does.
REAL_CLEAN_SENTENCES = [
    # 264, Reflection, head 05186239
    "No revenue, RevOps, enterprise integration, compliance, or operator-experience concerns "
    "were identified in the reviewed change.",
    # 265, Gemini, head 48128080
    "No bugs or security issues were found in the workflow configuration, script logic, or "
    "test suite.",
    # 265, Gemini, head 88b68fc9 (its first sentence is still listed: see below)
    "No bugs or production risks were found.",
    # 266, Reflection, head f7787b7d
    "No GTM, RevOps, compliance, or operability concerns were identified in the reviewed files.",
    # 266, Reflection, head 5ec37b92: no concern word (main never listed it either)
    "No merge-blocking findings were identified from this review.",
    # Greptile on #267: kinds and places joined by "and", with and without a comma
    "No security and performance issues were found.",
    "No security, performance, and privacy issues were found in the scripts and tests.",
    # the shortest forms
    "No concerns.",
    "No issues found.",
]

#: Sentences shaped like those above that still name a concern: each must be listed.
LOOKALIKE_CONCERNS = [
    "No issues were found in the parser, but the writer could lose rows.",
    # "and" joins kinds, never clauses: each part must still be a kind or a place
    "No security issues and the writer drops rows were found.",
    "No bugs and no tests were found.",
    "No security issues were found in the scripts and the writer that loses rows.",
    # council delta 5 on #267: after a concern noun only a plain "or" joins kinds
    "No bugs, and security issues were found.",
    "No regressions, and data issues were found in the scripts.",
    "There were no bugs, and data issues were found.",
    "No problems, and production issues were found.",
    "No concerns, and security risks were identified in the workflow.",
    "I found no bugs, and security issues in the scripts.",
    "We found no regressions, and data issues in the code and tests.",
    "No bugs and security issues were found.",
    "No regressions and production issues were found in the scripts and tests.",
    "No bugs, security issues were found.",
    "No bugs, or security issues were found.",
    "No concerns were identified, except that the retry drops writes after a 429.",
    "I found no issue in the client; the outbox may replay a write twice.",
    "No retry was added, so the sync loses writes on a 429.",
    "No tests cover the path where the sync crashes.",
    "The change drops the retry, so a 429 loses the write.",
    "The change adds a cache, but it serves stale scores.",
    "This PR adds a sync that could leak the token.",
    "The change makes the workflow pass even when the script fails, which is wrong.",
    "Security issues were found in the token handling.",
    "No retry guards the crash path issues.",
    "No backoff where bugs were found before.",
    "No handling for rate-limit issues was found in the client.",
    "No tests for the data-loss bugs were found.",
    "This PR fails to release the lock on error.",
    "The diff fails to handle a 429.",
    "The pull request's tests fail on Windows.",
    "The changes to the retry fail on every 429.",
    # council on #267: a "nothing found" clause that runs on into a concern
    "No tests were added and three bugs were found.",
    "No rollback exists, and two data-loss bugs were identified in the outbox.",
    "No input sanitization exists and injection issues were found.",
    "No retry is in place and duplicate write issues were found in the sync.",
    "No tests covering known bugs were found.",
    "No lock is held and race issues were detected.",
    "I found no tests, and the new writer has two bugs.",
    "We found no regression tests and the retry path has known bugs.",
    "I found no rollback test and two regressions in the writer.",
    "We found no locking and the outbox has duplicate-write issues.",
    "Retries run without any identified backoff, causing duplicate writes and data loss issues.",
    "The PR merges the outbox without an identified owner, leaving rollback gaps.",
    "Writes go out without any found idempotency key, so duplicates are a problem.",
    # council on #267: a failure the change causes, stated about the change
    "This PR fails on Windows.",
    "The change silently fails when the CRM returns 429.",
    "This PR leaves the writer without retries and fails silently on 429.",
    "The change makes CI fail on every push.",
    "The diff failed the existing migration tests.",
    "These changes cause the nightly job to fail when the token expires.",
    "This patch fails the upgrade on databases written by main.",
    "The change is a failure for tenants with no CRM id.",
    # council delta on #267 (149c9277): quantities, comma splices and clauses as "kinds"
    "No fewer than three bugs were found.",
    "No less than two issues were found in the outbox.",
    "No fewer than five regressions were identified.",
    "No rate-limiting, two bugs were found.",
    "No idempotency key, three issues were found.",
    "No upgrade path, three regressions were identified.",
    "No owner, two security issues were raised.",
    "No one fixed two bugs found in review.",
    "No reviewer caught two bugs found in the outbox.",
    "No bugs were found in the parser, but the writer has two more.",
    "We found no workaround, two bugs remain in the writer.",
    "I see no migration, upgrade issues are certain.",
    "I found no owner, so two bugs stay open.",
    "The writer retries without an identified backoff, rate-limit issues.",
    "Writes go out without any identified idempotency key or dedupe issues.",
    # council delta on #267: a failure the change causes, in the shape of an intended one
    "This PR marks the sync failure as a success.",
    "The change reports a failure on every green run.",
    "This PR posts a failure comment on every successful run.",
    "The change records a failure when the CRM write succeeds.",
    "The change makes the whole sync fail when an enrichment call fails.",
    "This PR lets the nightly job fail when one tenant fails.",
    "The change marks a failure for every tenant without a CRM id.",
    "- No tests were added and three bugs were found.",
    "No tests were added \u2013 three bugs were found.",
    "No one\u2019s fix covers two bugs found.",
    # council delta on #267 (8f61fc18): a reported failure inside a negation or reversal
    "This PR no longer reports a failure when the sync fails.",
    "The change does not report a failure when the CRM write fails.",
    "The change never records a failure when the outbox write fails.",
    "This PR stops posting a failure if setup fails.",
    "This PR only marks a failure when the first tenant fails.",
    "The change records the failure when the write fails as a success.",
    "This PR silently skips posting a failure when the metrics script fails.",
    "The change no longer posts a failure comment when the review job fails.",
    "This PR removes the step that posts a failure if setup fails.",
    "This PR does not mask script failures.",
    # council delta on #267: the place looked names the concern or runs on
    "No other issues were found beyond the unbounded retry loop in the CRM writer.",
    "No further bugs were found beyond the unguarded outbox write in sync.py.",
    "No other issues were found on top of the off-by-one in pagination.",
    "I found no other issues beyond the unchecked tenant id in the writer.",
    "We see no further problems beyond the N-plus-one query in the scorer.",
    "No bugs were found in the reader, the writer can double-post invoices.",
    "No security issues were found on the read side, the write side needs a lock.",
    "No issues were found for single-tenant runs, multi-tenant runs share a cache.",
    "No issues were found in the parser, the writer silently discards rows on 429.",
    "No regressions were found at the API layer, the CLI ignores the new flag.",
    "No idempotency key or retry-safety issues were found, nor tests.",
    "No obvious bugs were found.",
    "No other issues were found.",
    # council delta on #267: an absence before "without an identified ... concern"
    "The change retries CRM writes without an idempotency key, without an identified test concern.",
    "This PR writes to the CRM without a lease check without an identified security concern.",
    # council delta on #267 (ec06fd93): a comma splice after the place looked
    "No bugs were found in the reader, writer doesn't retry.",
    "No security issues were found in auth, tokens never expire.",
    "No bugs were found in the reader, writer untested.",
    "No issues were found in the parser, writes never retry.",
    "No bugs were found in the reader, it never retries.",
    "No issues were found in the reader, nothing guards writes.",
    "No security issues were found in the API, webhooks unauthenticated.",
    "I found no bugs in the reader, writer double-posts invoices.",
    "No regressions were found in the CLI, sync ignores tenants.",
    "No issues were found within the scorer, cache never invalidates.",
    "No bugs were found in the reader Writer double-posts, rows vanish",
    "No security issues were found in auth Tokens never expire",
    # council delta on #267: hedged kinds leave room for the concerns not named
    "No high-severity issues were found.",
    "No apparent bugs were found.",
    "No immediate issues were found.",
    "No substantive issues were found.",
    "No severe issues were found in the writer.",
    "No merge-blocking issues were found.",
    "No new bugs were found.",
    "No known regressions were found.",
    "No big problems were found.",
    # council delta on #267: a defect before "without an identified ... concern"
    "The change disables the lease check without an identified security concern.",
    "This PR bypasses the tenant filter without an identified security concern.",
    "The change ignores 429 responses without an identified correctness concern.",
    "This PR hardcodes the API token without an identified security concern.",
    "The change swallows every CRM exception without an identified correctness concern.",
    "The reviewed files retry CRM writes forever without an identified correctness concern.",
    "This PR deletes outbox rows on rollback without an identified data concern.",
    "The change writes each invoice twice without an identified correctness concern.",
]


@pytest.mark.parametrize("sentence", REAL_CLEAN_SENTENCES)
def test_a_sentence_that_finds_nothing_or_describes_the_change_is_no_concern(
    sentence: str,
) -> None:
    """The #264-#266 summaries' own sentences are not listed as concerns."""
    assert reply.summary_concerns(f"Adds a sync. {sentence}") == []


@pytest.mark.parametrize("sentence", LOOKALIKE_CONCERNS)
def test_a_concern_beside_a_nothing_found_clause_or_a_description_is_still_listed(
    sentence: str,
) -> None:
    """A concern is listed however the sentence starts; only a clause finding nothing is unread."""
    assert reply.summary_concerns(f"Adds a sync. {sentence}") == [sentence]


#: The whole summaries of the #264-#266 replies that found nothing and name no
#: concern in any sentence; the other replies' summaries hold a sentence below.
REAL_CLEAN_SUMMARIES = [
    (
        "The change adds aggregate model token-usage reporting to review summaries, logs, and "
        "hidden summary markers across the Gemini and Reflection reviewers. "
        + REAL_CLEAN_SENTENCES[0]
    ),
    (
        "This PR adds the Review labels workflow and scripts/check_review_labels.py to require "
        "an accepted: or rejected: reply from a repository member on every automated review "
        "finding thread, and dispatches it from the Gemini and Reflection review workflows. "
        + REAL_CLEAN_SENTENCES[1]
    ),
    (
        "The change adds a read-only weekly workflow and script that aggregate review-loop "
        "metrics from merged pull requests into a job summary and retained JSON artifact. "
        + REAL_CLEAN_SENTENCES[3]
    ),
]

#: The #264-#266 sentences that are still listed, by design: each reads the same
#: as a concern ("makes the whole sync fail when a call fails", "no other issues
#: beyond the unbounded retry loop", "no longer posts a failure if setup fails").
#: When in doubt a sentence is listed; a harmless one listed costs a glance.
REAL_STILL_LISTED = [
    # 265, Reflection, head 48128080: "without an identified ... concern" after a
    # description reads the same as "disables the lease check without an identified
    # security concern"
    "The reviewed files establish the workflow, permission, and status-posting behavior "
    "without an identified revenue, RevOps, integration, governance, operability, security, "
    "operator-experience, contract, or edge-case concern.",
    # 265, Gemini, head 88b68fc9
    "The incremental changes add a check_run trigger for Greptile Review, mark the commit "
    "status as pending before workflow setup (posting a failure if setup fails), and switch "
    "head_reviews to query the combined commit status and filtered Greptile check runs.",
    # 265, Reflection, head 88b68fc9
    "I found no GTM, RevOps, enterprise-integration, compliance, or operator-facing issue "
    "beyond the covered correctness tests.",
    # 266, Gemini, head 5ec37b92
    "This incremental change adds `set -o pipefail` to the `loop-metrics` workflow so `tee` "
    "does not mask script failures, records the UTC `since` and `until` timestamps in the "
    "saved JSON output, and adds corresponding unit tests for both changes.",
    # 266, Reflection, head 5ec37b92
    "The change records the UTC reporting window in the saved JSON and makes the workflow fail "
    "when the metrics script fails behind tee.",
]


@pytest.mark.parametrize("sentence", REAL_STILL_LISTED)
def test_a_real_sentence_that_reads_like_a_concern_is_still_listed(sentence: str) -> None:
    """The #264-#266 sentences the rule cannot tell from a concern stay listed."""
    assert reply.summary_concerns(f"Adds a sync. {sentence}") == [sentence]


def test_a_concern_split_off_at_an_abbreviation_is_still_listed() -> None:
    """Council delta on #267: "e.g." ends a sentence for the splitter; the rest is still read."""
    listed = reply.summary_concerns(
        "Adds a sync (e.g. no idempotency key, three issues were found)."
    )
    assert listed == ["no idempotency key, three issues were found)."]


@pytest.mark.parametrize(
    "summary",
    [
        "No " + "a, " * 20_000 + "bugs were found.",
        "I found no " + "word " * 40_000 + "x.",
        ("without an identified " + "a " * 25) * 5_000,
        "The change " + "posts a x failure if the y z fails " * 5_000,
        "No bugs were found in " * 10_000 + "x.",
        "No bugs were found in " + "the x, " * 50_000 + "y.",
    ],
    ids=["no-labels", "found-no-words", "without", "intended-failure", "tails", "tail-labels"],
)
def test_a_long_hostile_summary_is_read_in_linear_time(summary: str) -> None:
    """Council delta on #267: the unanchored "without" took 28 s on 360k characters."""
    started = time.perf_counter()
    reply.summary_concerns(summary)
    assert time.perf_counter() - started < 1.0
