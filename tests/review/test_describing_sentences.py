"""Summary sentences that describe the change are no concern; real concerns stay listed.

The described sentences come from live reviews: Reflection on gtm_toolkit #271, and the
reviews of #264-#266. Listing too much is safe; listing too little hides a concern, so
every lookalike below must still be listed.
"""

import pytest

from devloop.review.reply import names_concern, summary_concerns

DESCRIBED = [
    # gtm_toolkit #271 (Reflection)
    "This closes the paths where a pull request's `.gitattributes` could hide changed content "
    "from the reviewers, with corresponding workflow and increment tests updated.",
    "The change hardens reviewed diffs by reading attributes from the empty tree, forcing text "
    "output, and never running external diff or textconv programs.",
    # gtm_toolkit #264-#266
    "The change extends loop metrics to capture failed model attempts.",
    "It also hardens the labels check to distinguish a failed reviewer from a pending one.",
    # the council's round four judged these descriptions: they say what is now recorded
    "This change extends usage to count failed requests.",
    "This change updates the usage report to count failed calls.",
    "It also updates the token metrics to count failed calls.",
    "It also updates the status label to distinguish a failed run from a passing one.",
    # a closing verb's relative clause names the hole that was closed
    "The change closes the gap where tokens could leak into the log.",
]

STILL_LISTED = [
    # Left listed on purpose: a live description (gtm_toolkit #264) with the same shape as
    # "This change updates the gate so that a failed check is still counted", which may be
    # a concern. Listing a description is safe; dropping a concern is not.
    "This incremental change updates the usage marker so that a failed attempt is still counted.",
    # phrased like a description, but a concern
    "This change updates the retry, which could loop forever if the API stalls.",
    "This closes the paths where X could hide content, but a symlink could still leak it.",
    "This change adds a cache that may serve stale scores.",
    "The change extends the sync, which fails when the CRM returns 429.",
    "It also hardens the check, and the writer could lose rows.",
    "This closes the gap where tokens could leak if the log level is debug.",
    "This change hardens the diff but could still hide binary files.",
    "This fixes the crash and loses rows on retry.",
    "This PR fixes the crash, though the writer may still drop rows.",
    "The change updates the parser so that it could hide a changed file.",
    "This change adds a failed-write counter that is never reset.",
    "The change moves the lock, so two writers could race.",
    "This change records failed attempts but leaks the token into the log.",
    # "could hide X if Y" and plain failure statements, with no description opening
    "A pull request's `.gitattributes` could hide changed content if it marks code binary.",
    "The writer could hide changed content from the reviewers.",
    "The sync crashes when the CRM returns 429.",
    "The job failed silently on the last run.",
    "The change could break callers of the old API.",
    "This closes the paths where tokens could leak, with no tests added.",
    # Gemini on dev-loop #31: closing or preventing a safeguard is the concern
    "This prevents the check that could catch leaks.",
    "This closes the fallback that would recover failed writes.",
    "This closes the paths where tokens could leak, with workflow tests disabled.",
]

#: The council's probe on this change: concerns phrased as descriptions, every one
#: listed on main. Each must stay listed.
COUNCIL = [
    "This fixes the crash — the writer now leaks the token into the log.",
    "This fixes the crash—but only for one writer.",
    "This fixes the crash (the writer still loses rows on retry).",
    "This prevents nothing: tokens still leak into the log.",
    "This fixes the crash: the retry now drops the last batch.",
    "This fixes the crash then drops the last batch on retry.",
    "This hardens the parser to drop malformed rows silently.",
    "This removes the lock to speed up writes at the cost of races between workers.",
    "This rejects valid tokens because the regex is wrong.",
    "This blocks all writes when the cache is stale.",
    "The change stops the sync when the token is stale, losing queued rows.",
    "This tightens the check so that valid rows are dropped.",
    "This removes the guard where tokens could leak into the log.",
    "This hardens the check in a way that drops valid rows.",
    "This fixes the crash with a workaround that loses rows on retry.",
    "This fixes the crash & leaks the token.",
    "This fixes the crash.The writer now loses rows.",
    "This removes the check for duplicate rows.",
    "This removes the leak detection from the log writer.",
    "The change removes the stale-cache guard from the scorer.",
    "It also removes the crash handler around the CRM write.",
    "This stops retrying failed writes after the first attempt.",
    "This stops validating tokens that leak into the log.",
    "This rejects every request with a stale token, including fresh retries.",
    "The fixes leak memory on every retry.",
    "The fixes in this PR could regress the sync.",
    "The new guards could be bypassed by a symlink.",
    "The blocks overwrite each other on retry.",
    "It blocks forever when the lock is stale.",
    "It stops working after the token goes stale.",
    "It fixes nothing; it leaks.",
    "It counts failed writes as successful.",
    "This change marks failed checks as passing.",
    "It reports failed runs as green.",
    "This change keeps failed jobs in the queue forever.",
    "The change sets failed builds to success.",
    "This change records failed attempts as zero tokens, under-billing the usage.",
    "It also lists failed reviewers as approved.",
    "THIS FIXES THE CRASH — IT NOW LEAKS TOKENS.",
    "This fixes the “crash” — the writer still loses rows.",
    "This fixes the crash in the sync loop that happened when the CRM returned a 429 "
    "during a paginated read of the opportunity table across several tenants in the "
    "nightly job — the writer still overwrites the newest value with a stale one on "
    "retry.",
    "This fixes the crash, but the writer may still drop rows.",
    "This fixes the crash and still loses rows.",
    "This change adds a cache that may serve stale scores.",
    "This fixes the crash; the writer still loses rows.",
]


@pytest.mark.parametrize("sentence", DESCRIBED)
def test_a_sentence_that_describes_the_change_is_no_concern(sentence: str) -> None:
    assert not names_concern(sentence)
    assert summary_concerns(f"Adds a sync. {sentence}") == []


#: The council's delta check on the templates: concerns that fit an early, looser
#: version of them, every one listed on main. Each must stay listed.
COUNCIL_DELTA = [
    "This fixes the race in the writer that the reader also has.",
    "This fixes a leak that the other three adapters share.",
    "This fixes a race that is reintroduced on every restart.",
    "This fixes the crash that none of the tests cover.",
    "This fixes the crash that the tests don't cover.",
    "This fixes the leak that isn't covered by a test.",
    "This fixes the crash that CI cannot reproduce.",
    "This fixes the crash by dropping the last batch.",
    "This fixes the crash at the cost of dropping rows.",
    "This prevents the crash by swallowing the error.",
    "This fixes the deadlock by disabling the lock entirely.",
    "This fixes the race by skipping duplicate rows.",
    "This fixes the crash for one of three writers.",
    "This fixes the leak in one of the two code paths.",
    "This prevents the leak detector from running.",
    "This prevents the crash handler from catching errors.",
    "This prevents the stale-cache check from firing.",
    "This closes the outbox that holds failed writes.",
    "This closes the duplicate-write guard for every tenant.",
    "This fixes the failed-write retry limit at zero.",
    "This prevents the retry of failed writes.",
    "This fixes the crash however the writer loses rows on retry.",
    "This fixes the crash whereas the retry path loses rows.",
    "This fixes the crash albeit by dropping rows.",
    "This fixes the crash nor does it stop the leak.",
    "This fixes the crash otherwise the writer drops rows.",
    "This fixes the crash…the writer loses rows.",
    "This fixes the crash；the writer now loses rows.",
    "This fixes the crash：the retry drops rows.",
    "This fixes the crash → rows are lost on retry.",
    'This fixes the crash "but" the writer loses rows.',
    "This fixes the crash `and` the writer loses rows.",
    "This fixes the crash。The writer loses rows.",
    "This fixes the crash, with error handling removed.",
    "This fixes the crash, with the retry disabled for every tenant.",
    "This fixes the leak, with no test added.",
    "This fixes the crash, with the writer losing rows.",
    "The change extends the counter to count failed writes twice.",
    "This change updates the scorer to count failed checks favourably.",
    "This change updates the gate to record failed reviews nowhere.",
    "This change makes the gate pass to record failed checks.",
    "It also hardens the labels check to distinguish a failed reviewer from a passing reviewer.",
    "This change updates the gate so that a failed check is still counted.",
    "This change updates the bill so that a failed attempt is still counted.",
    "THIS FIXES THE CRASH BY DROPPING THE LAST BATCH.",
    "This fixes the crash by dropping the last batch.",
    "This fixes the `crash` by `dropping` the last batch.",
]


#: The council's final delta check: concerns and borderline sentences that fit the
#: second version of the templates, every one listed on main. Each must stay listed.
COUNCIL_FINAL = [
    "This closes the gap that a retry could reopen.",
    "This closes the race that a restart could reintroduce.",
    "This closes the hole that a forged header could bypass.",
    "This prevents the leak that a second writer could reintroduce.",
    "This closes the gap that a concurrent retry might reopen.",
    "This closes the outbox that would retry failed writes.",
    "This prevents the retry that would recover failed writes.",
    "This prevents the cleanup that would release leaked connections.",
    "This closes the fallback path that would replay lost rows.",
    "This prevents the check that would catch stale scores.",
    "This closes the race that might resurface on restart.",
    "This closes the happy path where a retry could double-bill.",
    "This closes the Postgres path where a retry could drop rows.",
    "This closes the paths where a retry could still double-bill.",
    "This closes the paths where a retry could yet double-bill.",
    "This closes the paths where a retry could double-bill, with no tests added.",
    "This closes the paths where a retry drops rows.",
    "This fixes the wrong crash.",
    "This fixes the wrong race.",
    "This PR fixes the wrong regression.",
    "This fixes a different crash.",
    "This fixes the other leak.",
    "This fixes the wrong deadlock in the writer.",
    "This fixes the first crash.",
    "This fixes the easier race.",
    "This prevents the visible hang.",
    "This fixes the secondary crash.",
    "This fixes the race in the mock.",
    "This fixes the leak in the tests.",
    "This fixes the crash on the CI runner.",
    "This fixes the crash on the failed path.",
    "This fixes the crash in the stale cache.",
    "This change updates the success counter to count failed checks.",
    "This change updates the pass rate to count failed checks.",
    "This change extends the approval tally to count failed reviewers.",
    "It also updates the uptime metric to count failed runs.",
    "This updates the green-build badge to count failed builds.",
    "This change updates the billable total to count failed requests.",
    "It also hardens the labels check to distinguish a failed reviewer from a failed one.",
    "It also hardens the labels check to distinguish a failed reviewer from a crashed one.",
    "This change updates the docs to distinguish a failed run from a green one.",
    "This closes the paths where a retry could double-bill, with xfail tests added.",
    "This closes the paths where a retry could double-bill, with fewer tests added.",
    "This closes the paths where a retry could double-bill, with the failing tests updated.",
    "This closes the paths where a retry could double-bill-not-always.",
]


#: The council's fourth round: safeguards named in a where-clause, weakened tests in the
#: tail, and borderline sentences. Every one listed on main; each must stay listed.
COUNCIL_ROUND4 = [
    "This closes the path where a failed write would be retried.",
    "This closes the window where an operator could recover lost rows.",
    "This prevents the cases where the CLI would report a failure.",
    "This closes the route where admins could revoke a leaked token.",
    "This prevents the cases where the guard would catch a stale lease.",
    "This closes the window where a reviewer could flag a regression.",
    "This prevents the cases where the job would exit nonzero on a crash.",
    "This closes the path where the scheduler might rerun a crashed job.",
    "This prevents the cases where the loader would raise on corrupt rows.",
    "This closes the window where the new lock could deadlock the writer.",
    "This prevents the cases where the loader would raise a KeyError on missing rows.",
    "This closes the gaps where tests could catch a regression.",
    "This closes the cases where a retry could double-bill in production today.",
    "This closes the paths where a retry could double-bill on Postgres alone.",
    "This closes the paths where a retry could double-bill on SQLite only.",
    "This closes the paths where a retry could double-bill except-on-Postgres.",
    "This closes the paths where a retry could double-bill, with weakened tests updated.",
    "This closes the paths where a retry could double-bill, with loosened tests updated.",
    "This closes the paths where a retry could double-bill, with commented-out tests updated.",
    "This closes the paths where a retry could double-bill, with mocked-out tests updated.",
    "This closes the paths where a retry could double-bill, with deleted tests updated.",
    "This closes the paths where a retry could double-bill, with relaxed tests updated.",
    "This closes the paths where a retry could double-bill, with ignored tests updated.",
    "This closes the paths where a retry could double-bill, with failing tests added.",
    "This change updates the success counter to count failed checks.",
    "This change updates the pass rate to count failed checks.",
    "It also hardens the gate to distinguish a failed write from a lost one.",
    "It also hardens the gate to distinguish a failed write from a stale one.",
    "This change updates the docs to distinguish a failed run from a green one.",
    "This closes the paths where a retry could double-bill; the writer still leaks.",
    "This closes the paths where a retry could double-bill. The writer leaks.",
    "This closes the paths where a retry could double-bill?",
    "It closes the paths where a retry could double-bill.",
    "This closes the paths where `retry and leak` could double-bill.",
    "This closes the paths where a retry could double‑bill and leak.",
]


@pytest.mark.parametrize(
    "sentence", STILL_LISTED + COUNCIL + COUNCIL_DELTA + COUNCIL_FINAL + COUNCIL_ROUND4
)
def test_a_concern_phrased_like_a_description_is_still_listed(sentence: str) -> None:
    assert names_concern(sentence)
    listed = summary_concerns(f"Adds a sync. {sentence}")
    assert listed and all(part in " ".join(sentence.split()) for part in listed)


@pytest.mark.parametrize(
    "summary",
    [
        "This closes the gap where " + "x " * 60_000 + "could leak.",
        "This fixes the " + "crash " * 60_000 + ".",
        "It also hardens " + "a failed " * 40_000 + "attempt.",
    ],
)
def test_a_long_hostile_description_is_read_in_linear_time(summary: str) -> None:
    import time

    started = time.perf_counter()
    summary_concerns(summary)
    assert time.perf_counter() - started < 1.0
