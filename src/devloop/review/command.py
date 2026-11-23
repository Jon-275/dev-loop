"""``devloop review``: review a pull request in CI, or the current branch locally.

In CI the working directory is the pull request's checkout, read only through Git, and
``--trusted`` is the base branch's checkout: ``devloop.toml``, the lenses and the review
feedback come from there, never from the pull request. A local review prints the summary
and each finding instead of posting, and needs only the reviewer's API key.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from devloop.config import BUILT_IN_REVIEWERS, Config, ConfigError, load_config
from devloop.review import github
from devloop.review.gitread import run_git
from devloop.review.model import ReviewError, Settings, ThreadMark, Usage
from devloop.review.providers import asker, lens_for, provider_for, resolve
from devloop.review.run import Policy, outcome, run_review
from devloop.review.threads import load_feedback

#: The variable ``review.yml`` hands the reviewer's key in, whatever the reviewer.
SHARED_KEY = "DEVLOOP_API_KEY"


def policy(config: Config) -> Policy:
    """Return the run policy a project's ``devloop.toml`` sets."""
    review = config.review
    return Policy(
        files=review.files,
        high_effort=review.high_effort,
        blocking=review.blocking_severities,
        routes=review.providers,
        tiers=config.tiers,
    )


def marker_names(config: Config) -> dict[str, str]:
    """Map every devloop reviewer's marker prefix to its name, for cross-reviewer threads."""
    names = set(BUILT_IN_REVIEWERS) | set(config.review.reviewers)
    names |= {n for tier in config.review.providers.values() for n in tier}
    found: dict[str, str] = {}
    for name in sorted(names):
        try:
            found[f"{name}-finding"] = resolve(name, config.review.reviewers).title
        except ReviewError:
            continue  # a hosted reviewer (greptile) or an incomplete table: no marker of ours
    return found


def review(
    name: str,
    trusted: Path,
    *,
    local: bool = False,
    base: str = "origin/main",
    env: Mapping[str, str] | None = None,
) -> int:
    """Run reviewer ``name``; return 0 when it passed, 1 when the check fails, 2 on bad setup."""
    source = dict(os.environ) if env is None else dict(env)
    try:
        config = load_config(trusted / "devloop.toml")
        reviewer = resolve(name, config.review.reviewers)
        if not source.get(reviewer.key) and source.get(SHARED_KEY):
            source[reviewer.key] = source[SHARED_KEY]  # review.yml passes every key this way
        if local:
            head = run_git(["rev-parse", "HEAD"]).strip()
            fork = run_git(["merge-base", base, head]).strip()
            settings = Settings(source.get(reviewer.key, ""), "", "", "", fork, head)
        else:
            settings = Settings.from_env(reviewer.key, source)
    except (ConfigError, ReviewError, OSError) as error:
        print(f"devloop review: {error}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as error:  # no such base, or not a repository
        print(
            f"devloop review: could not read the branch ({type(error).__name__})", file=sys.stderr
        )
        return 2
    provider = provider_for(reviewer, lens_for(trusted, config.review.lenses_dir, name))
    usage = Usage(run=settings.run_id)
    feedback = load_feedback(
        provider,
        trusted / config.review.feedback if config.review.feedback else None,
        trusted / config.review.lessons if config.review.lessons else None,
    )
    ask = asker(reviewer, provider, settings.api_key, usage)
    names = marker_names(config)
    open_ids: list[set[int]] = []

    def is_open(mark: ThreadMark) -> bool:
        """Return whether an own thread is unresolved, asking GitHub once."""
        if mark.comment_id is None:
            return False
        if not open_ids:
            open_ids.append(github.open_thread_comment_ids(settings))
        return mark.comment_id in open_ids[0]

    def post(body: str, comments: list[dict[str, Any]]) -> None:
        """Post the review, or print it for a local run."""
        if not local:
            github.post_review(settings, provider, body, comments)
            return
        print(body)
        for comment in comments:
            print(
                f"\n--- {comment['path']}:{comment['line']} ({comment['side']})\n{comment['body']}"
            )

    def run() -> str:
        """Run the review with the CI or the local outside steps."""
        try:
            return run_review(
                settings,
                provider,
                policy(config),
                name=name,
                ask=ask,
                known=set if local else lambda: github.known_threads(settings, provider, names),
                post=post,
                feedback=feedback,
                previous=None if local else lambda: github.previous_review(settings, provider),
                usage=usage,
                is_open=is_open,
            )
        finally:
            if usage.used():  # a failed review's tokens, and failed attempts, are billed too
                print(usage.line())

    return outcome(provider, run)


def main(argv: Sequence[str]) -> int:
    """Parse ``devloop review`` arguments and run the review."""
    import argparse

    parser = argparse.ArgumentParser(prog="devloop review", description=__doc__)
    parser.add_argument("--provider", required=True, help="the reviewer to run (gemini, ...)")
    parser.add_argument(
        "--trusted", default=".", help="the trusted checkout holding devloop.toml (default: .)"
    )
    parser.add_argument("--local", action="store_true", help="review this branch and print")
    parser.add_argument("--base", default="origin/main", help="local mode's base (origin/main)")
    args = parser.parse_args(list(argv))
    return review(args.provider, Path(args.trusted), local=args.local, base=args.base)
