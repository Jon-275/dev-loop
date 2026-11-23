"""The review's summary comment: nothing raised is hidden, and the markers come last."""

from review_helpers import PROVIDER

from devloop.review.body import review_body
from devloop.review.markdown import code_place
from devloop.review.model import Finding, Increment, NotPosted, Review, ThreadMark, Usage
from devloop.review.threads import Suppression

BUG = Finding("P1", "app.py", 2, "Token logged", "b")
NIT = Finding("P3", "app.py", 9, "Name is vague", "b")


def test_a_clean_review_says_so_and_ends_with_its_marker() -> None:
    body = review_body(PROVIDER, Review("Adds a sync.", ()), "low", 0, base="b" * 40)
    assert body.startswith("### Testbot review (`thinking: low`)")
    assert PROVIDER.no_findings in body
    assert body.splitlines()[-1] == (
        f"<!-- testbot-review findings=0 posted=0 not_posted=0 unreviewed=0 base={'b' * 40} -->"
    )


def test_every_finding_not_given_a_thread_is_still_named() -> None:
    other = ThreadMark("Greptile", path="app.py", line=3, url="https://g/1")
    review = Review("S.", (BUG, NIT), not_posted=(NotPosted("Rows may not load", "no line"),))
    body = review_body(
        PROVIDER,
        review,
        "high",
        1,
        known=[BUG],
        also_raised=[(BUG, other)],
        suppressed=[(NIT, Suppression("f", 4, "https://r/1"))],
        summary_only=[NIT],
    )
    assert "**Findings:** 1 P1, 0 P2, 1 P3." in body
    assert "_1 of them already have a thread from an earlier Testbot review" in body
    assert "- P1 `app.py:2` Token logged: also raised by [Greptile's thread](https://g/1)" in body
    assert "suppressed (rejected 4 times, [last rejection](https://r/1))." in body
    assert "Not blocking (P3), so listed here without a thread" in body
    assert "- P3 `app.py:9` Name is vague" in body
    assert "- Rows may not load _(not posted: no line)_" in body


def test_concerns_without_findings_never_read_as_no_findings() -> None:
    body = review_body(PROVIDER, Review("S.", (), not_posted=(NotPosted("x", "y"),)), "low", 0)
    assert PROVIDER.no_findings not in body
    assert "**No inline findings**, but the concerns below were raised." in body


def test_an_incomplete_review_names_every_file_not_reviewed_safely() -> None:
    path = "docs/a`<!--hide.py"
    body = review_body(PROVIDER, Review("S.", (), unreviewed=((path, "cut off"),)), "high", 0)
    assert "**Incomplete review:** 1 changed file(s) were not reviewed" in body
    assert f"- {code_place(path)}: cut off" in body
    assert body.count("<!--") == 1


def test_usage_goes_before_the_summary_marker_and_only_when_a_model_was_asked() -> None:
    usage = Usage(run="42")
    usage.add(10, 2)
    body = review_body(PROVIDER, Review("S.", ()), "low", 0, base="b" * 40, usage=usage)
    assert body.splitlines()[-1].startswith("<!-- testbot-review ")
    assert "<!-- model-usage: requests=1 input=10 output=2 unreported=0 failed=0 run=42 -->" in body
    for unused in (None, Usage()):
        assert "model-usage" not in review_body(PROVIDER, Review("S.", ()), "low", 0, usage=unused)


def test_an_incremental_review_says_what_it_left_out_and_why_the_counts_differ() -> None:
    increment = Increment("e" * 40, (("lib.py", 2),), ("old.py",), 3, 2, 1, 2)
    body = review_body(PROVIDER, Review("S.", ()), "low", 0, base="b" * 40, increment=increment)
    assert "**Incremental review** of the changes since `eeeeeeeeeeee`" in body
    assert "has 3 hunk(s) in 1 file(s); the pull request's whole diff" in body
    assert "a new file is one hunk in the whole diff however many places" in body
    assert "- `lib.py` (2 hunk(s))" in body
    assert "1 file(s) changed since then are back to the base version" in body
    assert body.splitlines()[-1].endswith(f"since={'e' * 40} -->")
