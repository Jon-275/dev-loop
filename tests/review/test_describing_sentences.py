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
    # a closing verb's object or relative clause names what was fixed
    "This PR fixes the crash in the sync loop.",
    "The change closes the gap where tokens could leak into the log.",
    "This prevents a race between the two writers.",
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


@pytest.mark.parametrize("sentence", STILL_LISTED + COUNCIL + COUNCIL_DELTA)
def test_a_concern_phrased_like_a_description_is_still_listed(sentence: str) -> None:
    assert names_concern(sentence)
    assert summary_concerns(f"Adds a sync. {sentence}") == [" ".join(sentence.split())]


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
