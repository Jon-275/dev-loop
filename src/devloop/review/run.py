"""Run one review: route it by tier, ask the model, place each finding, and post.

The steps that touch the outside (the model, the threads, posting, the last review) are
passed in, so a CI run, a local run and the tests share everything else.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase
from typing import Any

from devloop.review.body import review_body
from devloop.review.gitread import (
    changed_files,
    diff_lines,
    every_changed,
    file_contents,
    renames,
    reviewable_blobs,
    run_git_diff,
    split_diff,
    unshown_files,
)
from devloop.review.increment import increment_note, plan
from devloop.review.model import (
    MAX_FILES,
    Baseline,
    Effort,
    Finding,
    IncompleteReview,
    Provider,
    ReviewError,
    Settings,
    ThreadMark,
    Usage,
)
from devloop.review.parts import review_in_parts
from devloop.review.prompt import is_high_effort, prompt_path
from devloop.review.threads import (
    Feedback,
    Suppression,
    anchor,
    is_own_duplicate,
    raised_by_other,
    suppression_for,
)

Ask = Callable[[str, Effort], str]
Post = Callable[[str, list[dict[str, Any]]], None]


@dataclass(frozen=True)
class Policy:
    """What a project's ``[review]`` and ``[tiers]`` decide for this run."""

    files: tuple[str, ...]
    high_effort: tuple[str, ...] = ("*",)
    blocking: tuple[str, ...] = ("P1", "P2")
    #: The reviewer names routed to each tier; an empty map routes every reviewer.
    routes: Mapping[int, tuple[str, ...]] = field(default_factory=dict)
    tiers: Mapping[int, tuple[str, ...]] = field(default_factory=dict)


def pr_tier(paths: Sequence[str], tiers: Mapping[int, tuple[str, ...]]) -> int:
    """Return the pull request's tier: its riskiest file's (1 if any matches tier 1).

    A file matching no ``[tiers]`` glob is tier 2; with no changed file the tier is 2.
    """
    found = [
        1
        if any(fnmatchcase(p, g) for g in tiers.get(1, ()))
        else 3
        if any(fnmatchcase(p, g) for g in tiers.get(3, ()))
        else 2
        for p in paths
    ]
    return min(found, default=2)


def routed(name: str, tier: int, routes: Mapping[int, tuple[str, ...]]) -> bool:
    """Return whether reviewer ``name`` reviews a tier-``tier`` pull request."""
    return not routes or name in routes.get(tier, ())


def run_review(
    settings: Settings,
    provider: Provider,
    policy: Policy,
    *,
    name: str,
    ask: Ask,
    known: Callable[[], set[ThreadMark]],
    post: Post,
    feedback: Feedback | None = None,
    clock: Callable[[], float] = time.monotonic,
    previous: Callable[[], Baseline | None] | None = None,
    usage: Usage | None = None,
    is_open: Callable[[ThreadMark], bool] = lambda mark: False,
) -> str:
    """Review the pull request and post the result; return what the log should say.

    ``name`` is the reviewer's key in ``policy.routes``. A pull request whose tier routes
    no review to it passes without a model request, and the status says why. Findings
    whose severity is not in ``policy.blocking`` are listed in the summary and get no
    thread. ``is_open`` says whether an own thread is unresolved (asked only for a
    reworded title).

    Raises:
        IncompleteReview: some changed files could not be reviewed; the partial review,
            naming them, was posted, and the check must still fail.
        ReviewError: the review could not be completed; the check must fail.

    """
    every = unshown_files(settings.base_sha, settings.head_sha, ())
    tier = pr_tier(every_changed(settings.base_sha, settings.head_sha), policy.tiers)
    if not routed(name, tier, policy.routes):
        status = f"Not routed: tier {tier} pull requests get no {provider.name} review"
        write_status(status)
        return f"{status}."
    if not settings.api_key:
        raise ReviewError(
            f"{provider.key_name} is not available to this run, so {provider.name} cannot "
            "review it. Set it as a repository secret; Dependabot pull requests also need "
            "it as a Dependabot secret."
        )
    changed = changed_files(settings.base_sha, settings.head_sha, policy.files)
    if not changed:
        return f"No reviewed file types changed; nothing for {provider.name} to review."
    span = f"{settings.base_sha}...{settings.head_sha}"
    full_diff = run_git_diff([span, "--", *policy.files])
    baseline = previous() if previous is not None else None
    todo = plan(baseline, settings.base_sha, settings.head_sha, policy.files, full_diff, changed)
    if todo.nothing_new:
        write_status(todo.nothing_new)
        return f"{todo.nothing_new}; {provider.name} posted no review."
    increment = todo.increment
    attached = reviewable_blobs(settings.head_sha, todo.paths)[:MAX_FILES]
    contexts = {
        p: f"--- FILE: {prompt_path(p)} ---\n{file_contents(settings.head_sha, p)}"
        for p in attached
    }
    skipped = [path for path in todo.paths if path not in attached]
    unshown = [path for path in every if path not in changed]
    effort: Effort = "high" if is_high_effort(changed, policy.high_effort) else "low"
    learned = Feedback() if feedback is None else feedback
    sections = split_diff(todo.diff)
    for path in todo.paths:  # a changed file the diff has no section for is still sent
        sections.setdefault(path, "")
    notes = learned.notes + (increment_note(increment) if increment else "")
    review = review_in_parts(
        provider, ask, effort, sections, contexts, skipped, notes, unshown, clock=clock
    )
    unseen = [p for p in todo.paths if not _shown(sections[p]) and p not in contexts]
    if unseen:  # neither its hunks nor its contents reached the model
        listed = {path for path, _ in review.unreviewed}
        review = replace(
            review,
            unreviewed=review.unreviewed
            + tuple((p, UNSEEN_REASON) for p in unseen if p not in listed),
        )
    marks = known() if review.findings else set()
    repeated: list[Finding] = []
    new: list[Finding] = []
    also_raised: list[tuple[Finding, ThreadMark]] = []
    suppressed: list[tuple[Finding, Suppression]] = []
    summary_only: list[Finding] = []
    for finding in review.findings:
        if is_own_duplicate(provider, finding, marks, is_open):
            repeated.append(finding)
            continue
        other = raised_by_other(provider, finding, marks)
        held = suppression_for(finding, learned)
        if other is not None:
            also_raised.append((finding, other))
            if finding.severity == "P1" and "P1" in policy.blocking:  # always a thread
                new.append(finding)
        elif held is not None:
            suppressed.append((finding, held))
        elif finding.severity not in policy.blocking:
            summary_only.append(finding)
        else:
            new.append(finding)
    placed = _place(provider, new, also_raised, full_diff)
    body = review_body(
        provider,
        review,
        effort,
        len(placed),
        repeated,
        also_raised,
        suppressed,
        base=settings.base_sha,
        increment=increment,
        usage=usage,
        summary_only=summary_only,
    )
    post(body, placed)
    if review.unreviewed:
        count = len(review.unreviewed)
        raise IncompleteReview(
            f"{provider.name} posted a partial review with {len(placed)} finding thread(s), "
            f"but {count} of {len(sections)} changed file(s) were not reviewed: "
            + "; ".join(f"{path} ({reason})" for path, reason in review.unreviewed),
            f"Incomplete: {count} of {len(sections)} changed files not reviewed; see the comment",
        )
    done = f"Posted {provider.name} review with {len(placed)} finding thread(s)."
    if increment is not None:
        status = f"Reviewed changes since {increment.since[:12]} (incremental); resolve any threads"
        write_status(status)
        return f"{done} {status}."
    return done


#: Why a file is unreviewed when the prompt carried neither its hunks nor its contents.
UNSEEN_REASON = "neither its diff nor its contents reached the reviewer (over the file limit)"


def _shown(section: str) -> bool:
    """Return whether a file's diff section shows the reviewer any changed line."""
    return any(line.startswith("@@") for line in section.splitlines())


def _place(
    provider: Provider,
    new: Sequence[Finding],
    also_raised: Sequence[tuple[Finding, ThreadMark]],
    full_diff: str,
) -> list[dict[str, Any]]:
    """Return an inline comment for each new finding, on the pull request's whole diff.

    Raises:
        ReviewError: a finding has no line in the diff to attach to.

    """
    lines = diff_lines(full_diff)  # threads go on the diff GitHub shows
    moved = renames(full_diff)
    comments = [anchor(provider, replace(f, path=moved.get(f.path, f.path)), lines) for f in new]
    notes = {id(f): mark for f, mark in also_raised if f.severity == "P1"}
    for finding, comment in zip(new, comments, strict=True):
        mark = notes.get(id(finding))
        if comment is not None and mark is not None:
            where = f"[{mark.reviewer}'s thread]({mark.url})" if mark.url else mark.reviewer
            comment["body"] += f"\n\n_Also raised by {where}._"
    if any(comment is None for comment in comments):
        raise ReviewError(
            f"{provider.name} reported findings but the diff has no line to attach them to: "
            + "; ".join(f"{f.severity} {f.path}:{f.line} {f.title}" for f in new)
        )
    return [comment for comment in comments if comment is not None]


def annotation(provider: Provider, message: str) -> str:
    """Return a GitHub ``::error`` workflow command for ``message``, escaped onto one line.

    ``%``, carriage returns and newlines are percent-encoded as GitHub requires, so text
    from a model or an API cannot start a second workflow command.
    """
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::error title={provider.status_context}::{escaped}"


def write_status(description: str, env: Mapping[str, str] | None = None) -> None:
    """Hand the commit status ``description`` to the workflow through ``$GITHUB_OUTPUT``.

    Only a description built here from counts is written (no path or model text), on one
    line, so it cannot add another output. Without ``GITHUB_OUTPUT`` nothing is written.
    """
    target = (os.environ if env is None else env).get("GITHUB_OUTPUT")
    if not target:
        return
    line = " ".join(description.split())[:140]
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(f"status_description={line}\n")


def outcome(provider: Provider, review: Callable[[], str]) -> int:
    """Run ``review`` and return 0 when it was posted, 1 when the check must fail.

    A failure is one ``::error`` annotation, never a traceback; an incomplete review
    also hands the workflow its status description (:func:`write_status`).
    """
    try:
        print(review())
    except ReviewError as error:
        print(annotation(provider, str(error)))
        if isinstance(error, IncompleteReview):
            write_status(error.status)
        return 1
    return 0
