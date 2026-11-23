"""The review's shared types: token usage, settings and errors."""

import pytest

from devloop.review.model import (
    IncompleteReview,
    ReviewError,
    Settings,
    SettingsError,
    Usage,
    token_count,
)

ENV = {
    "GITHUB_TOKEN": "t",
    "GITHUB_REPOSITORY": "o/r",
    "PR_NUMBER": "1",
    "BASE_SHA": "b",
    "HEAD_SHA": "h",
    "GITHUB_RUN_ID": "4507913",
    "GEMINI_API_KEY": "k",
}


def test_usage_sums_answered_requests_and_names_the_unreported_ones() -> None:
    usage = Usage()
    usage.add(1000, 200)
    usage.add(None, 50)
    usage.add(2500, 300)
    assert (usage.requests, usage.unreported) == (3, 1)
    assert (usage.input_tokens, usage.output_tokens) == (3500, 500)
    assert usage.line() == (
        "Model usage: 3 request(s), 3,500 input and 500 output tokens (reasoning included); "
        "1 request(s) reported no usage, so the total is low."
    )
    assert (
        usage.marker()
        == "<!-- model-usage: requests=3 input=3500 output=500 unreported=1 failed=0 -->"
    )


def test_failed_attempts_are_counted_and_said_to_be_missing_from_the_total() -> None:
    usage = Usage()
    assert not usage.used()
    usage.fail()
    assert usage.used() and usage.requests == 0
    assert usage.line().endswith(
        "; 1 attempt(s) failed; any tokens billed for them are not in the total."
    )


def test_usage_rejects_counts_that_are_not_non_negative_ints() -> None:
    usage = Usage()
    counts: list[tuple[object, object]] = [(-5, 10), (True, 10), (10, 2.5)]
    for given, made in counts:
        usage.add(given, made)
    assert (usage.requests, usage.unreported, usage.input_tokens, usage.output_tokens) == (
        3,
        3,
        0,
        0,
    )
    assert token_count(0) == 0 and token_count(False) is None


@pytest.mark.parametrize(
    ("run", "tail"),
    [("4507913", " run=4507913 -->"), ("", " failed=0 -->"), ("12\n34", " failed=0 -->")],
    ids=["run-id", "no-run", "not-digits"],
)
def test_the_usage_marker_names_the_run_only_by_a_numeric_id(run: str, tail: str) -> None:
    assert Usage(requests=1, run=run).marker().endswith(tail)


def test_settings_are_read_with_the_providers_own_key() -> None:
    settings = Settings.from_env("GEMINI_API_KEY", ENV)
    assert (settings.api_key, settings.run_id, settings.local) == ("k", "4507913", False)
    assert Settings.from_env("REFLECTION_API_KEY", ENV).api_key == ""
    assert Settings.from_env("GEMINI_API_KEY", {**ENV, "GITHUB_RUN_ID": ""}).run_id == ""


def test_missing_environment_is_reported_by_name() -> None:
    env = {"GITHUB_REPOSITORY": "o/r", "BASE_SHA": "a", "HEAD_SHA": "b"}
    with pytest.raises(SettingsError, match="GITHUB_TOKEN, PR_NUMBER"):
        Settings.from_env("GEMINI_API_KEY", env)


def test_a_review_without_a_pull_request_is_local() -> None:
    assert Settings("k", "", "", "", "a", "b").local


def test_an_incomplete_review_is_a_review_error_with_a_short_status() -> None:
    error = IncompleteReview("long log message naming files", "Incomplete: 1 of 2")
    assert isinstance(error, ReviewError) and error.status == "Incomplete: 1 of 2"
