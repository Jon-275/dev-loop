"""Retries, and reviewing a diff too long for one reply in parts.

A reply cut off at the output limit is asked again in smaller parts; a part is asked only
while its worst case, retries included, still fits the job's time budget. Every changed
file ends up reviewed or named as unreviewed, never silently left out.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping, Sequence

from devloop.review.markdown import code_place
from devloop.review.model import (
    ATTEMPTS,
    BACKOFF_SECONDS,
    JOB_OVERHEAD_SECONDS,
    LINE_WINDOW,
    MAX_DIFF_CHARS,
    REQUEST_TIMEOUT_SECONDS,
    SEVERITIES,
    TITLE_SIMILARITY,
    Effort,
    Finding,
    Provider,
    ReplyCutOff,
    Review,
    ReviewError,
)
from devloop.review.prompt import build_prompt, prompt_path
from devloop.review.reply import normalise_title, parse_review, title_similarity


def retry_waits(attempts: int = ATTEMPTS) -> list[float]:
    """Return the waits, in seconds, before each of the ``attempts - 1`` retries."""
    return [BACKOFF_SECONDS * 2**retry for retry in range(attempts - 1)]


def worst_case_model_seconds(
    attempts: int = ATTEMPTS, timeout_seconds: float = REQUEST_TIMEOUT_SECONDS
) -> float:
    """Return the longest the model step can take: every attempt times out, every wait is slept."""
    return attempts * timeout_seconds + sum(retry_waits(attempts))


def with_retries[T](
    call: Callable[[], T],
    provider: Provider,
    *,
    attempts: int = ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    on_failure: Callable[[BaseException], None] | None = None,
) -> T:
    """Return the result of ``call``, retried with backoff while the provider is unavailable.

    While ``call`` raises an error ``provider.is_transient`` accepts, it is tried
    up to ``attempts`` times (by default 3: 2 retries), waiting
    ``BACKOFF_SECONDS`` and then twice as long each time; the last error is
    raised. Any other error is raised at once. The log names only the status
    or exception type (``provider.describe_error``). ``on_failure`` is called
    with every error ``call`` raises, retried or not, before it is classified, so
    a caller can count attempts that returned nothing (:meth:`Usage.fail`).
    """
    waits = retry_waits(attempts)
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as error:  # classified below; anything not transient is re-raised
            if on_failure is not None:
                on_failure(error)
            if attempt == attempts or not provider.is_transient(error):
                raise
            wait = waits[attempt - 1]
            print(
                f"{provider.name} is unavailable ({provider.describe_error(error)}, attempt "
                f"{attempt} of {attempts}); retrying in {wait:.0f} s",
                file=sys.stderr,
            )
            sleep(wait)
    raise AssertionError("unreachable: the last attempt returns or raises")


def request_failed(
    provider: Provider, error: BaseException, attempts: int = ATTEMPTS
) -> ReviewError:
    """Return the ``ReviewError`` for a final request error: its status or type, and the tries."""
    tries = f" after {attempts} attempts" if provider.is_transient(error) else ""
    return ReviewError(f"{provider.name} request failed{tries}: {provider.describe_error(error)}")


#: The review job's ``timeout-minutes`` in ``review.yml``: 40, so a
#: split review has room for further parts, each with its retries (945 s worst case).
JOB_TIMEOUT_MINUTES = 40
#: The time the model step may use in all, across every part of a split review:
#: the job less its other steps. A further part is asked only while its
#: worst case, every retry included (:func:`worst_case_model_seconds`), still fits.
MODEL_BUDGET_SECONDS = JOB_TIMEOUT_MINUTES * 60 - JOB_OVERHEAD_SECONDS
#: Why a file is unreviewed when its reply is cut off even on its own, at low effort.
CUT_OFF_REASON = "the reply was cut off at the output limit even for this file on its own"
#: Why a file is unreviewed when the time budget ran out before its part was asked.
OUT_OF_TIME_REASON = "the job's time budget ran out before this part could be reviewed"
#: The most other parts' file names a part's prompt lists; the rest are counted.
MAX_OTHER_PART_FILES = 50


def size_parts(paths: Sequence[str], sizes: Mapping[str, int], limit: int) -> list[list[str]]:
    """``paths`` in order, grouped so each group's diff fits ``limit`` characters.

    A file whose diff alone is over the limit is a group of its own.
    """
    groups: list[list[str]] = []
    total = 0
    for path in paths:
        size = sizes.get(path, 0)
        if groups and total + size <= limit:
            groups[-1].append(path)
            total += size
        else:
            groups.append([path])
            total = size
    return groups


def merge_reviews(reviews: Sequence[tuple[Sequence[str], Review]]) -> Review:
    """One review from the replies for parts of the diff.

    Findings are kept in severity order. A finding that repeats one from an
    earlier part (same severity and path, within :data:`LINE_WINDOW` lines,
    alike titles) is dropped from the threads but listed in ``merged``; a
    part's own findings are never compared with each other, so two findings
    one reply reports apart stay apart. A single
    part's summary is kept as it is; several are listed by the files they cover.
    """
    if len(reviews) == 1:
        return reviews[0][1]
    kept: list[Finding] = []
    merged: list[Finding] = []
    for _, review in reviews:
        earlier = list(kept)  # one reply's own findings are never merged with each other
        for finding in review.findings:
            if any(
                other.severity == finding.severity
                and other.path == finding.path
                and abs(other.line - finding.line) <= LINE_WINDOW
                and (
                    normalise_title(other.title) == normalise_title(finding.title)
                    or title_similarity(other.title, finding.title) >= TITLE_SIMILARITY
                )
                for other in earlier
            ):
                merged.append(finding)
            else:
                kept.append(finding)
    order = {name: rank for rank, name in enumerate(SEVERITIES)}
    kept.sort(key=lambda f: order[f.severity])
    summary = "\n".join(
        [
            f"The diff was too long for one reply, so it was reviewed in {len(reviews)} parts:",
            *(
                f"- {', '.join(code_place(path) for path in paths)}: "
                + " ".join(review.summary.split())
                for paths, review in reviews
            ),
        ]
    )
    return Review(
        summary=summary,
        findings=tuple(kept),
        not_posted=tuple(c for _, review in reviews for c in review.not_posted),
        merged=tuple(merged),
    )


def review_in_parts(
    provider: Provider,
    ask: Callable[[str, Effort], str],
    effort: Effort,
    sections: Mapping[str, str],
    contexts: Mapping[str, str],
    unattached: Sequence[str],
    notes: str,
    unshown: Sequence[str] = (),
    *,
    clock: Callable[[], float] = time.monotonic,
    budget_seconds: float = MODEL_BUDGET_SECONDS,
) -> Review:
    """Review every file in ``sections``, splitting the diff when a reply is cut off.

    The files are first grouped so each request's diff fits :data:`MAX_DIFF_CHARS`.
    A group whose reply stops at the output limit (:class:`ReplyCutOff`) is split
    in half and each half asked again; a single file is asked once more at low
    effort, then listed as unreviewed. After the first request, a part is asked
    only while its worst case, retries included, fits the time budget; the rest
    are unreviewed. A file whose diff alone is over the limit is reviewed in its
    first :data:`MAX_DIFF_CHARS` characters and listed as unreviewed. When a
    later part fails with another :class:`ReviewError`, it and the parts not yet
    asked are listed as unreviewed with that error, so the parts already
    reviewed are still posted; with no part reviewed yet, it is raised.

    ``contexts`` maps a path to its full contents block; ``unattached`` are the
    changed files sent without one; ``unshown`` (changed files the diff leaves
    out, :func:`unshown_note`) is named in every part's prompt, as are the
    files other parts cover. Every changed file ends up reviewed or in the
    result's ``unreviewed`` (one entry per file), never silently left out.
    """
    started = clock()
    paths = list(sections)
    sizes = {path: len(text) for path, text in sections.items()}
    pending: list[tuple[list[str], Effort]] = [
        (group, effort) for group in size_parts(paths, sizes, MAX_DIFF_CHARS)
    ]
    split = len(pending) > 1
    done: list[tuple[list[str], Review]] = []
    unreviewed: dict[str, str] = {
        path: f"only the first {MAX_DIFF_CHARS:,} characters of its diff were reviewed"
        for path in paths
        if sizes[path] > MAX_DIFF_CHARS
    }
    asked = 0
    while pending:
        group, level = pending.pop(0)
        if asked and clock() - started + worst_case_model_seconds() > budget_seconds:
            unreviewed.update(dict.fromkeys(group, OUT_OF_TIME_REASON))
            continue
        scope = ""
        if split:
            scope = (
                "\nThis is one part of a review split by file because the whole diff is too "
                f"long for one reply. Review only these files: "
                f"{', '.join(prompt_path(path) for path in group)}\n"
            )
            others = [prompt_path(path) for path in paths if path not in group]
            if others:
                listed = ", ".join(others[:MAX_OTHER_PART_FILES])
                more = len(others) - MAX_OTHER_PART_FILES
                scope += (
                    f"Other parts review these changed files, so a change to them is not "
                    f"missing: {listed}" + (f" (and {more} more)" if more > 0 else "") + "\n"
                )
        prompt = build_prompt(
            provider,
            "".join(sections[path] for path in group),
            [contexts[path] for path in group if path in contexts],
            [path for path in group if path in unattached],
            notes + scope,
            unshown,
        )
        asked += 1
        try:
            done.append((group, parse_review(provider, ask(prompt, level))))
        except ReplyCutOff as cut:
            names = ", ".join(prompt_path(path) for path in group)  # one line, no commands
            print(f"{cut}; reviewing {names} in smaller parts", file=sys.stderr)
            split = True
            if len(group) > 1:
                half = len(group) // 2
                pending[:0] = [(group[:half], level), (group[half:], level)]
            elif level == "high":
                pending.insert(0, (group, "low"))
            else:
                unreviewed[group[0]] = CUT_OFF_REASON
        except ReviewError as error:
            if not done:
                raise
            print(f"{error}; listing the parts not reviewed", file=sys.stderr)
            for path in [*group, *(path for rest, _ in pending for path in rest)]:
                unreviewed[path] = f"its request failed: {error}"
            pending.clear()
    done.sort(key=lambda part: paths.index(part[0][0]))
    if done:
        merged = merge_reviews(done)
    else:
        merged = Review(summary=f"{provider.name} could not review any changed file.", findings=())
    order = {path: rank for rank, path in enumerate(paths)}
    return Review(
        summary=merged.summary,
        findings=merged.findings,
        not_posted=merged.not_posted,
        unreviewed=tuple(sorted(unreviewed.items(), key=lambda item: order[item[0]])),
        merged=merged.merged,
    )
