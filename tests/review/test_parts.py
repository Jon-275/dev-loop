"""Retries and merging the parts of a split review."""

import pytest
from review_helpers import PROVIDER

from devloop.review import parts
from devloop.review.model import ATTEMPTS, Finding, Review


def test_a_transient_error_is_retried_with_backoff_and_every_failure_is_reported() -> None:
    outcomes: list[BaseException | str] = [TimeoutError(), TimeoutError(), "ok"]
    waits: list[float] = []
    failed: list[BaseException] = []

    def call() -> str:
        """Raise or return the next scripted outcome."""
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    assert parts.with_retries(call, PROVIDER, sleep=waits.append, on_failure=failed.append) == "ok"
    assert waits == [15.0, 30.0] and len(failed) == 2


def test_a_lasting_or_final_error_is_raised_after_its_attempts() -> None:
    failed: list[BaseException] = []

    def timeout() -> str:
        """Raise a timeout every time."""
        raise TimeoutError

    def refused() -> str:
        """Fail in a way the provider says is final."""
        raise ValueError("bad request")

    with pytest.raises(TimeoutError):
        parts.with_retries(timeout, PROVIDER, sleep=lambda _: None, on_failure=failed.append)
    assert len(failed) == ATTEMPTS
    slept: list[float] = []
    with pytest.raises(ValueError):
        parts.with_retries(refused, PROVIDER, sleep=slept.append, on_failure=failed.append)
    assert slept == []
    assert len(failed) == ATTEMPTS + 1
    message = str(parts.request_failed(PROVIDER, TimeoutError()))
    assert message == f"Testbot request failed after {ATTEMPTS} attempts: TimeoutError"


def test_every_attempt_and_wait_fits_inside_the_job_limit() -> None:
    worst = parts.worst_case_model_seconds()
    assert worst == ATTEMPTS * 300 + 15 + 30
    assert worst <= parts.MODEL_BUDGET_SECONDS < parts.JOB_TIMEOUT_MINUTES * 60


def test_one_parts_nearby_findings_are_never_merged_with_each_other() -> None:
    first = Review("A.", (Finding("P2", "crm.py", 10, "Retry failed CRM writes", "b"),))
    own = (
        Finding("P2", "sync.py", 10, "Retry failed CRM writes", "retry"),
        Finding("P2", "sync.py", 12, "Log failed CRM writes", "log"),
    )
    merged = parts.merge_reviews([(["crm.py"], first), (["sync.py"], Review("B.", own))])
    assert [f.body for f in merged.findings if f.path == "sync.py"] == ["retry", "log"]
    assert merged.merged == ()


def test_files_are_grouped_to_fit_the_limit() -> None:
    sizes = {"a.py": 40, "b.py": 40, "c.py": 200, "d.py": 10}
    assert parts.size_parts(list(sizes), sizes, 100) == [["a.py", "b.py"], ["c.py"], ["d.py"]]
