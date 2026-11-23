"""The review's top-level comment: the summary, the counts and everything not posted.

Nothing the reviewer raised is hidden: findings held by an earlier thread, raised by
another reviewer, suppressed by feedback, below the blocking severities, or merged from
another part of the review are listed by name, as are concerns that are not findings and
files no reply covered.
"""

from __future__ import annotations

from collections.abc import Sequence

from devloop.review.markdown import code_place, safe_markdown
from devloop.review.model import (
    SEVERITIES,
    Finding,
    Increment,
    Provider,
    Review,
    ThreadMark,
    Usage,
)
from devloop.review.prompt import MAX_UNSHOWN_FILES
from devloop.review.threads import Suppression

#: The most carried-over files an incremental summary names, and the most characters
#: of names in each list (GitHub's body limit is 65,536); the rest are counted.
MAX_CARRIED_FILES = 200
MAX_NAMED_CHARS = 20_000


def _within(items: Sequence[tuple[str, int]], count: int, chars: int) -> list[tuple[str, int]]:
    """Return the first ``items`` whose names fit in ``count`` entries and ``chars`` characters."""
    kept: list[tuple[str, int]] = []
    for item in items[:count]:
        chars -= len(code_place(item[0])) + 20
        if chars < 0:
            break
        kept.append(item)
    return kept


def review_body(
    provider: Provider,
    review: Review,
    effort: str,
    posted: int,
    known: Sequence[Finding] = (),
    also_raised: Sequence[tuple[Finding, ThreadMark]] = (),
    suppressed: Sequence[tuple[Finding, Suppression]] = (),
    *,
    base: str = "",
    increment: Increment | None = None,
    usage: Usage | None = None,
    summary_only: Sequence[Finding] = (),
) -> str:
    """Return the review's top-level comment: summary, counts and how the gate works.

    Nothing the reviewer raised is hidden: findings that already have a thread
    (``known``), that another reviewer already raised, that review feedback
    suppresses or that another part of the review also reported are listed by
    name; concerns that are not findings are listed with their reasons; and
    files no reply covered are named, under a heading that says the review is
    incomplete. "No findings" is said only when there is none of these. So is
    what an ``increment`` left out; the marker records the ``base`` reviewed.
    ``summary_only`` findings are below the project's blocking severities: they are
    listed here and get no thread, so they never hold up a merge.
    """
    counts = ", ".join(
        f"{sum(1 for f in review.findings if f.severity == s)} {s}" for s in SEVERITIES
    )
    lines = [
        f"{provider.header} (`{provider.effort_label}: {effort}`)",
        "",
    ]
    if increment is not None:
        shown = _within(increment.carried, MAX_CARRIED_FILES, MAX_NAMED_CHARS)
        # The two counts are of different diffs (that head to this one, the base to
        # this one), so the first can be larger: PR #266 edited new files in 5 places.
        paragraph = (
            f"**Incremental review** of the changes since `{increment.since[:12]}`, the last "
            f"head {provider.name} reviewed, with changes merged in from the base branch left "
            f"out. This round's diff, from that head, has {increment.hunks} hunk(s) in "
            f"{increment.files} file(s); the pull request's whole diff, from the base branch, "
            f"has {increment.total_hunks} hunk(s) in {increment.total_files} file(s)."
        )
        if increment.hunks > increment.total_hunks:
            paragraph += (
                " This round's count is the larger because the two are different diffs: for "
                "example, a new file is one hunk in the whole diff however many places this "
                "round edited it."
            )
        if increment.carried:
            paragraph += (
                f" {len(increment.carried)} file(s) with "
                f"{sum(n for _, n in increment.carried)} hunk(s) were reviewed at that head and "
                "are unchanged, so they were not reviewed again; their threads stay as they are:"
            )
        lines += [paragraph, *(f"- {code_place(path)} ({n} hunk(s))" for path, n in shown)]
        if len(shown) < len(increment.carried):
            lines += [f"- and {len(increment.carried) - len(shown)} more file(s)"]
        left = MAX_NAMED_CHARS - sum(len(code_place(path)) + 20 for path, _ in shown)
        restored = _within([(p, 0) for p in increment.restored], MAX_UNSHOWN_FILES, left)
        more = len(increment.restored) - len(restored)
        if increment.restored:
            lines += [
                f"{len(increment.restored)} file(s) changed since then are back to the base "
                "version, so nothing is left to review in them"
                + "".join(f", {code_place(path)}" for path, _ in restored).replace(",", ":", 1)
                + (f" (and {more} more)" if more else "")
                + "."
            ]
        lines += [""]
    if review.unreviewed:
        lines += [
            f"**Incomplete review:** {len(review.unreviewed)} changed file(s) were not "
            f"reviewed, so the {provider.status_context} check fails. No finding below "
            "covers them:",
        ]
        lines += [
            f"- {code_place(path)}: {safe_markdown(reason, 300)}"
            for path, reason in review.unreviewed
        ]
        lines += [""]
    lines += [safe_markdown(review.summary, 20_000), ""]

    def item(finding: Finding) -> str:
        """One finding as a list entry: severity, place and title."""
        title = safe_markdown(finding.title, 300).replace("\n", " ")
        return f"- {finding.severity} {code_place(finding.path, finding.line)} {title}"

    if review.findings:
        lines += [
            f"**Findings:** {counts}. Each blocking one is an inline thread. Fix it or reply, then "
            "resolve the thread: `main` merges only when every conversation is resolved.",
        ]
        if known:
            lines += [
                "",
                f"_{len(known)} of them already have a thread from an earlier {provider.name} "
                "review, so no new thread was opened for them:_",
                *(item(finding) for finding in known),
            ]
        if also_raised:
            lines += [
                "",
                "Also raised by another reviewer, so no new thread was opened "
                "(except for a P1, which always gets its own thread):",
            ]
            for finding, mark in also_raised:
                where = f"[{mark.reviewer}'s thread]({mark.url})" if mark.url else mark.reviewer
                too = "; posted too, as a P1" if finding.severity == "P1" else ""
                lines += [f"{item(finding)}: also raised by {where}{too}."]
        if suppressed:
            lines += ["", "Suppressed by review feedback, so no new thread was opened:"]
            for finding, held in suppressed:
                why = f"rejected {held.rejections} times"
                if held.url:
                    why += f", [last rejection]({held.url})"
                lines += [f"{item(finding)}: suppressed ({why})."]
        if summary_only:
            levels = ", ".join(sorted({f.severity for f in summary_only}))
            lines += [
                "",
                f"Not blocking ({levels}), so listed here without a thread; fix them if "
                "they are worth it:",
                *(item(finding) for finding in summary_only),
            ]
    elif review.not_posted:
        lines += ["**No inline findings**, but the concerns below were raised."]
    elif review.unreviewed:
        lines += ["**No findings in the files that were reviewed.**"]
    else:
        lines += [provider.no_findings]
    if review.merged:
        lines += [
            "",
            f"{len(review.merged)} finding(s) were reported by more than one part of the "
            "review and merged into the thread above:",
            *(item(finding) for finding in review.merged),
        ]
    if review.not_posted:
        lines += [
            "",
            f"**Not posted as findings** ({len(review.not_posted)}): no thread was opened, "
            "so check each one by hand:",
        ]
        for concern in review.not_posted:
            title = safe_markdown(concern.title, 600).replace("\n", " ")
            reason = safe_markdown(concern.reason, 600).replace("\n", " ")
            lines += [f"- {title} _(not posted: {reason})_"]
    if usage is not None and usage.used():
        lines += ["", f"_{usage.line()}_", usage.marker()]
    lines += [
        "",
        f"<!-- {provider.summary_marker} findings={len(review.findings)} posted={posted} "
        f"not_posted={len(review.not_posted)} unreviewed={len(review.unreviewed)} base={base}"
        + (f" since={increment.since}" if increment else "")
        + " -->",
    ]
    return "\n".join(lines)
