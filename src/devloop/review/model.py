"""The shared types of ``devloop review``: settings, findings, replies, usage and errors.

Ported from gtm_toolkit's ``scripts/review_core.py``. Each provider (Gemini, an
OpenAI-compatible API, Anthropic) supplies only a :class:`Provider`; everything else is
shared, so every reviewer runs, posts, de-duplicates, fails and is tested the same way.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Self

MAX_FILES = 15
MAX_DIFF_CHARS = 120_000
MAX_CONTEXT_CHARS = 250_000
MAX_COMMENTS_PER_REVIEW = 50
#: The login a workflow's ``GITHUB_TOKEN`` posts as: every devloop reviewer.
BOT_LOGIN = "github-actions[bot]"
#: Review apps that post as themselves, by login, with the name the summary gives them.
OTHER_REVIEW_APPS = {"greptile-apps[bot]": "Greptile"}
MAX_COMMENT_CHARS = 60_000
SEVERITIES = ("P1", "P2", "P3")
#: A finding within this many lines of an earlier thread of the same reviewer,
#: severity and path is that thread's finding (reworded or moved a little).
LINE_WINDOW = 5
#: A finding within this many lines of another reviewer's open thread on the
#: same path is left to that thread.
CROSS_REVIEW_WINDOW = 3
#: An own thread with no activity for this many days no longer holds a finding.
OWN_THREAD_DAYS = 30
#: How alike two titles must be (word overlap) for an open thread to hold a
#: reworded finding. A resolved thread holds only the same title.
TITLE_SIMILARITY = 0.3
#: Repository roles whose ``accepted:`` reply marks a thread as fixed.
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

#: HTTP statuses worth retrying: the request timed out (408), rate limited (429,
#: unless a quota or credit is used up), or the service overloaded or failing.
TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})
#: How many times a model request is tried (so ``ATTEMPTS - 1`` retries).
ATTEMPTS = 3
#: The wait before the first retry, doubled for each later one (15, 30 s).
BACKOFF_SECONDS = 15.0
#: The longest one model request may take before it is abandoned and retried.
#: A non-streaming request sends nothing until the review is done; observed
#: high-effort reviews of large pull requests take up to about 190 s.
REQUEST_TIMEOUT_SECONDS = 300
#: What a review job needs besides the model: checkouts, the install, git and GitHub calls.
JOB_OVERHEAD_SECONDS = 120

Effort = Literal["high", "low"]


class ReviewError(RuntimeError):
    """The review could not be completed; the check fails with this message."""


class ReplyCutOff(ReviewError):
    """The model's reply stopped at its output-token limit, so it is incomplete.

    :func:`run_review` then reviews the diff again in smaller parts
    (:func:`review_in_parts`); any other ``ReviewError`` fails the check at once.
    """


def token_count(value: object) -> int | None:
    """``value`` as a token count, or ``None`` when it is not a count a provider could report.

    Only a plain non-negative ``int`` is a count: ``None``, a ``bool``, a negative
    number or any other type is treated as not reported, never coerced.
    """
    if type(value) is int and value >= 0:
        return value
    return None


@dataclass
class Usage:
    """The tokens one review's model requests used, as the provider reported them.

    Each answered request is counted, a cut-off one included (its tokens are
    billed too). A request whose reply carries no usage, or a count that is not
    a non-negative ``int`` (:func:`token_count`), is counted as ``unreported``.
    Every attempt that raised (a timeout or dropped connection that
    ``with_retries`` repeated, or the final error) is counted as ``failed``: it
    returned no usage, so whatever was billed for it is not in the total, and the
    sentence says so. A total is never presented as complete when it is not.

    ``run`` is the Actions run id (``GITHUB_RUN_ID``). The marker carries it so a
    reader can match the summary to the run that set the reviewer's commit status,
    whose ``target_url`` names the same run; it is left out unless it is all digits.
    """

    requests: int = 0
    unreported: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failed: int = 0
    run: str = ""

    def add(self, input_tokens: object, output_tokens: object) -> None:
        """Count one answered request; a missing or invalid count means it was not reported."""
        self.requests += 1
        given, made = token_count(input_tokens), token_count(output_tokens)
        if given is None or made is None:
            self.unreported += 1
            return
        self.input_tokens += given
        self.output_tokens += made

    def fail(self) -> None:
        """Count one attempt that raised instead of answering (its usage is unknown)."""
        self.failed += 1

    def used(self) -> bool:
        """Return whether any request was sent: answered, or attempted and failed."""
        return bool(self.requests or self.failed)

    def line(self) -> str:
        """One sentence for the log and the summary, built from counts only."""
        text = (
            f"Model usage: {self.requests} request(s), {self.input_tokens:,} input and "
            f"{self.output_tokens:,} output tokens (reasoning included)"
        )
        if self.unreported:
            text += f"; {self.unreported} request(s) reported no usage, so the total is low"
        if self.failed:
            text += (
                f"; {self.failed} attempt(s) failed; any tokens billed for them are not "
                "in the total"
            )
        return text + "."

    def marker(self) -> str:
        """Return a hidden marker with the same counts, for scripts that read the summary."""
        return (
            f"<!-- model-usage: requests={self.requests} input={self.input_tokens} "
            f"output={self.output_tokens} unreported={self.unreported} failed={self.failed}"
            + (f" run={self.run}" if self.run.isascii() and self.run.isdigit() else "")
            + " -->"
        )


class IncompleteReview(ReviewError):
    """Part of the diff could not be reviewed; the partial review was posted.

    ``status`` is the commit status description: counts only, no paths and no
    model text, so it is safe for ``$GITHUB_OUTPUT``.
    """

    def __init__(self, message: str, status: str) -> None:
        """Keep the log ``message`` and the short ``status`` description."""
        super().__init__(message)
        self.status = status


class SettingsError(ReviewError):
    """The run's environment lacks a setting; the command cannot run (exit 2)."""


@dataclass(frozen=True)
class Settings:
    """What one review run needs: the key, the pull request and where to post.

    ``github_token`` and ``pr_number`` are empty for a local review, which posts nothing.
    """

    api_key: str
    github_token: str
    repository: str
    pr_number: str
    base_sha: str
    head_sha: str
    #: The Actions run id (``GITHUB_RUN_ID``), empty outside Actions; the usage marker names it.
    run_id: str = ""

    @property
    def local(self) -> bool:
        """Return whether the review only prints its findings (no pull request to post on)."""
        return not self.pr_number

    @classmethod
    def from_env(cls, key_name: str, env: Mapping[str, str] | None = None) -> Self:
        """Read the settings for a CI review, failing with the names of any missing variables.

        The API key (variable ``key_name``) may be empty here; the review fails the check
        on it, so the status says why.

        Raises:
            SettingsError: a required variable is missing or empty.

        """
        source = dict(os.environ) if env is None else dict(env)
        required = ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "PR_NUMBER", "BASE_SHA", "HEAD_SHA")
        missing = [name for name in required if not source.get(name)]
        if missing:
            raise SettingsError(f"missing environment variables: {', '.join(missing)}")
        return cls(
            api_key=source.get(key_name, ""),
            github_token=source["GITHUB_TOKEN"],
            repository=source["GITHUB_REPOSITORY"],
            pr_number=source["PR_NUMBER"],
            base_sha=source["BASE_SHA"],
            head_sha=source["HEAD_SHA"],
            run_id=source.get("GITHUB_RUN_ID", ""),
        )


@dataclass(frozen=True)
class Provider:
    """What one reviewer adds to the shared machinery: names, checks and its lens."""

    #: The reviewer's name in messages ("Gemini", "Reflection").
    name: str
    #: The commit status and annotation title ("Gemini review").
    status_context: str
    #: The review comment's heading.
    header: str
    #: The hidden marker prefix on this reviewer's threads ("gemini-finding").
    marker_prefix: str
    #: The hidden summary marker ("gemini-review").
    summary_marker: str
    #: The environment variable holding the API key.
    key_name: str
    #: The label of the effort setting in the summary heading ("thinking").
    effort_label: str
    #: True when retrying the error may succeed (never for an exhausted quota).
    is_transient: Callable[[BaseException], bool]
    #: A log-safe name for an error: its status or type, never its text.
    describe_error: Callable[[BaseException], str]
    #: The reviewer's role and focus: the opening of the prompt.
    lens: str
    #: Extra per-finding field instructions, after ``severity``.
    finding_fields: str = ""
    #: The prompt's instruction for the ``summary`` field.
    summary_rule: str = (
        "summary: two or three sentences on what changed and what the findings mean; do not "
        "say whether it is safe to merge."
    )
    #: The summary text when there are no findings.
    no_findings: str = "**No findings.** LGTM."
    #: The allowed ``dimension`` tags and their names, or ``None`` for none.
    dimensions: Mapping[str, str] | None = None


@dataclass(frozen=True)
class Finding:
    """One problem a reviewer found."""

    severity: Literal["P1", "P2", "P3"]
    path: str
    line: int
    title: str
    body: str
    #: The review dimension (a lens's own tags, such as D1-D9), or ``None``.
    dimension: str | None = None


@dataclass(frozen=True)
class NotPosted:
    """A concern the reviewer raised that is not a finding, with the reason."""

    title: str
    reason: str


@dataclass(frozen=True)
class Review:
    """A reviewer's parsed reply, or several parts' replies merged (:func:`merge_reviews`)."""

    summary: str
    findings: tuple[Finding, ...]
    #: Concerns that are not findings (no file and line, or outside the lens), with reasons.
    not_posted: tuple[NotPosted, ...] = ()
    #: Changed files no reply covered, each with the reason.
    unreviewed: tuple[tuple[str, str], ...] = ()
    #: Findings dropped as the same finding reported by another part of the review.
    merged: tuple[Finding, ...] = ()


@dataclass
class DiffLines:
    """The lines of one file a review comment can be attached to."""

    right: set[int] = field(default_factory=set)  # added or context lines (new file)
    left: set[int] = field(default_factory=set)  # deleted lines (old file)


@dataclass(frozen=True)
class ThreadMark:
    """An existing review thread a new finding may duplicate.

    For this reviewer's own threads, ``key`` and ``line`` come from the hidden
    marker (``legacy`` from the older title-based marker). For another
    reviewer's open thread, ``path`` and ``line`` are where it sits.
    """

    reviewer: str
    path: str = ""
    line: int | None = None
    key: str = ""
    legacy: str = ""
    url: str = ""
    #: This reviewer's thread: its normalised title ("" when it has no heading)
    #: and its first comment's id, for the open-thread check.
    title: str = ""
    comment_id: int | None = None
