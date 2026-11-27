"""The ``devloop`` command line.

Exit codes: 0 success (or a report without ``--check``), 1 a check failed, 2 the
command could not run (bad configuration or sources), 3 nothing failed but something
could not be judged (``doctor --repo``).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from devloop import changelog, changes, main_health, pr_open
from devloop.config import CONFIG_FILE, Config, ConfigError, load_config
from devloop.doctor import diagnose
from devloop.init import init
from devloop.rules import base_config
from devloop.size import run as run_size
from devloop.sync import Change, SyncError, apply

CONFIG_FROM_HELP = (
    "in CI, read devloop.toml from this base revision (HEAD^1 on a merge ref), "
    "so a pull request is not judged by rules it changed"
)


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="devloop", description="A standard review and delivery loop across coding agents."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("init", "write devloop.toml if absent and render the layout for every tool"),
        ("sync", "re-render AGENTS.md blocks, skills and adapters from their sources"),
        ("doctor", "check the project is set up and nothing has drifted"),
        ("size", "report a pull request's size (or, with --base, a branch's) against pr.max_lines"),
        ("changelog", "check changelog fragments, or build them into the changelog"),
        ("changes", "in CI, say whether a pull request changes only documentation"),
        ("pr-open", "refuse a push to a branch whose PR already merged or closed"),
        ("main-health", "on a push to main, open or close the workflow's 'main is red' issue"),
        ("review", "review a pull request with a model and post each finding as a thread"),
        ("feedback", "collect accepted and rejected review findings into review.feedback"),
        ("labels", "in CI, check every review finding on a pull request has a labelled reply"),
        ("gate", "in CI, post the one merge status of a pull request: its lane and its reviews"),
        ("lessons", "rank the bug classes review found and write the lessons and rules"),
        ("metrics", "measure the review loop over the pull requests merged in a window"),
        ("agent", "add an opted-in commit trailer or install its commit-msg hook"),
        ("council", "check the council's role cards, or plan which roles review a change"),
        ("bench", "run a coding agent on the conformance benchmark's tasks (local only)"),
        ("release-notes", "print a version's section of the changelog, as its release notes"),
        ("release-due", "in CI, say whether a push to main is a new version to tag and publish"),
    ):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--root", type=Path, default=Path.cwd(), help="project root")
        if name == "doctor":
            sub.add_argument("--check", action="store_true", help="exit 1 on any problem")
            sub.add_argument(
                "--repo",
                metavar="OWNER/NAME",
                help="instead, compare the repository's GitHub settings with [github] (read-only)",
            )
            sub.add_argument("--json", action="store_true", help="with --repo, print records")
        if name in ("size", "changes"):
            event = os.environ.get("GITHUB_EVENT_NAME", "")
            sub.add_argument("--event", default=event, help="github.event_name")
            sub.add_argument("--config-from", metavar="REV", help=CONFIG_FROM_HELP)
        if name == "size":
            sub.add_argument("--base", help="measure this branch locally from where it left BASE")
        if name == "lessons":
            notes = os.environ.get("REVIEW_NOTES_DIR") or None
            sub.add_argument("--notes", type=Path, default=notes, help="council notes folder")
        if name == "metrics":
            sub.add_argument("--days", type=int, default=7, help="the window, in days")
            sub.add_argument("--json", type=Path, default=None, help="also write the figures")
        if name == "release-notes":
            sub.add_argument("--version", help="X.Y.Z (default: the version in pyproject.toml)")
        if name == "release-due":
            sub.add_argument("--output", required=True, type=Path, help="write the notes here")
        if name == "gate":
            sub.add_argument("--pr", required=True, type=int, help="the pull request number")
            sub.add_argument("--repo", required=True, help="owner/name")
            sub.add_argument("--run-url", default="", help="the run's URL, linked from the status")
            sub.add_argument(
                "--context",
                default=None,
                help="the status name the workflow expects; do not start if gate.context differs",
            )
        if name == "main-health":
            sub.add_argument("--workflow", required=True, help="the workflow's name")
            sub.add_argument("--sha", required=True, help="the commit the run tested")
            sub.add_argument("--run-url", required=True, help="the run's URL")
            sub.add_argument("--needs", required=True, help="toJSON(needs)")
        if name == "changelog":
            actions = sub.add_subparsers(dest="action", required=True)
            check = actions.add_parser("check", help="validate the fragments")
            check.add_argument("--pr", action="store_true", help="note code with no entry")
            check.add_argument("--config-from", metavar="REV", help=CONFIG_FROM_HELP)
            build = actions.add_parser("build", help="move the fragments into the changelog")
            build.add_argument("--dry-run", action="store_true", help="print, write nothing")
    return parser


def _main_health(args: argparse.Namespace) -> int:
    """Bring the workflow's main-red issue in line with this run."""
    try:
        needs = json.loads(args.needs)
    except ValueError:
        needs = None
    if (
        not isinstance(needs, dict)
        or not needs
        or not all(isinstance(job, dict) for job in needs.values())
    ):
        print(
            "devloop: --needs has no job results; list the workflow's jobs in needs",
            file=sys.stderr,
        )
        return 2
    try:
        done = main_health.report(main_health.run_gh, args.workflow, args.sha, args.run_url, needs)
    except subprocess.CalledProcessError as exc:
        reason = (exc.stderr or "").strip().splitlines()
        command = " ".join(exc.cmd[:2]) if isinstance(exc.cmd, list) else "gh"
        print(f"devloop: {command} failed: {reason[0] if reason else exc}", file=sys.stderr)
        return 2
    print(f"{args.workflow} at {args.sha[:12]}: {done}")
    return 0


def _rules(root: Path, rev: str | None) -> Config:
    """Return the configuration that judges the change: the base's at ``rev`` when given.

    Without ``rev`` it is the working tree's file, as a local run reads it. A base with
    no ``devloop.toml`` falls back to the working tree's and prints that it did.
    """
    if rev is None:
        return load_config(root / CONFIG_FILE)
    config, note = base_config(root, rev)
    if note:
        print(note)
    return config


def _write_changes(code: bool, tests: Sequence[str]) -> None:
    """Write ``code`` and ``doc_tests`` to the GitHub Actions output, or print them."""
    lines = [f"code={'true' if code else 'false'}", f"doc_tests={' '.join(tests)}"]
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


def _changes(args: argparse.Namespace, root: Path) -> int:
    """Write ``code`` and ``doc_tests`` for GitHub Actions; 0 unless the rules are unreadable.

    On a pull request, ``--config-from`` reads the rules from the base. If they cannot
    be read the answer is "code changed" and the exit code is 2: never docs-only.
    """
    on_pr = args.event == "pull_request"  # no other event is ever docs-only
    try:
        config = _rules(root, args.config_from if on_pr else None)
    except ConfigError:
        if on_pr and args.config_from is not None:
            _write_changes(True, [])
        raise
    docs = config.docs
    skills = (config.skills.project, *config.agents.skills_targets)
    code, files = changes.decide(args.event, root, docs, skills)
    tests = [] if code else changes.doc_tests(root, docs)
    if code:
        print("Code changed (or the change could not be read): every check runs.")
    else:
        print(f"Only documentation changed ({len(files)} files): {', '.join(files)}")
        print(f"The {len(tests)} test files that read documents still run.")
    _write_changes(code, tests)
    return 0


def _changelog(args: argparse.Namespace, root: Path) -> int:
    """Check or build the changelog fragments; return 1 only for invalid fragments."""
    if args.action == "build":
        log = load_config(root / CONFIG_FILE).changelog
        try:
            result = changelog.build(root, log, dry_run=args.dry_run)
        except ValueError as exc:
            print(f"devloop changelog: {exc}", file=sys.stderr)
            return 1
        if args.dry_run:
            print(result, end="")
        return 0
    log = _rules(root, args.config_from).changelog
    errors = changelog.check(root, log)
    for error in errors:
        print(f"::error title=Changelog fragment::{error}", file=sys.stderr)
    if not errors:
        print(f"devloop changelog: {len(changelog.fragment_files(root, log))} fragment(s) valid.")
    if args.pr:
        try:
            changed = changelog.changed_files(root)
        except (OSError, UnicodeDecodeError, subprocess.CalledProcessError) as exc:
            changed = None
            print(f"devloop changelog: cannot read the change ({exc}); skipping the note.")
        if changed is not None and changelog.needs_entry(changed, log):
            print(
                "::notice title=Changelog::This pull request changes code but adds no "
                f"{log.dir}/<slug>.<section>.md fragment (see {log.dir}/README.md)."
            )
    return 1 if errors else 0


def _summary(title: str, lines: list[str]) -> None:
    """Append ``lines`` to the GitHub Actions step summary, when there is one."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"## {title}\n\n```\n" + "\n".join(lines) + "\n```\n")


def _size(args: argparse.Namespace, root: Path) -> int:
    """Print the PR's size; return 1 only for an unapproved oversize PR."""
    config = _rules(root, args.config_from)
    code, lines = run_size(
        args.event, os.environ.get("GITHUB_EVENT_PATH"), root, config, os.environ, args.base
    )
    print("\n".join(lines))
    if code:
        print(f"::error title=PR size::{lines[-1]}", file=sys.stderr)
    _summary("PR size", lines)
    return code


def _report(changes: list[Change]) -> None:
    """Print each change, or that there was nothing to do."""
    for change in changes:
        print(f"{change.action} {change.path}")
    if not changes:
        print("up to date")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return its exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["pr-open"]:  # the guard parses its own arguments
        return pr_open.main(argv[1:])
    if argv[:1] == ["agent"]:  # the hook parses its own arguments
        from devloop.agent import main as agent_main

        return agent_main(argv[1:])
    if argv[:1] == ["council"]:  # the council parses its own arguments
        from devloop.council import main as council_main

        return council_main(argv[1:])
    if argv[:1] == ["bench"]:  # the benchmark parses its own arguments
        from devloop.bench.runner import main as bench_main

        return bench_main(argv[1:])
    if argv[:1] == ["review"]:  # the review parses its own arguments
        from devloop.review.command import main as review_main

        return review_main(argv[1:])
    args = _parser().parse_args(argv)
    root: Path = args.root.resolve()
    if args.command == "feedback":
        from devloop.review.feedback import main as feedback_main

        return feedback_main(root, os.environ)
    if args.command == "labels":
        from devloop.review.labels import main as labels_main

        return labels_main(root, os.environ)
    if args.command == "gate":
        from devloop.gate.reader import main as gate_main

        return gate_main(root, args.repo, args.pr, args.run_url, os.environ, args.context)
    if args.command in ("release-notes", "release-due"):
        from devloop import release

        try:
            if args.command == "release-notes":
                return release.main_notes(root, args.version)
            return release.main_due(root, args.output, os.environ)
        except ConfigError as exc:
            print(f"devloop: {exc}", file=sys.stderr)
            return 2
    if args.command == "lessons":
        from devloop.lessons import main as lessons_main

        return lessons_main(root, args.notes)
    if args.command == "metrics":
        from devloop.metrics import main as metrics_main

        return metrics_main(root, args.days, args.json, os.environ)
    if args.command == "doctor" and (args.repo is not None or args.json):
        if args.repo is None:
            print("devloop doctor: --json needs --repo", file=sys.stderr)
            return 2
        from devloop.repo_settings import main as settings_main

        return settings_main(root, args.repo, args.json, os.environ)
    try:
        if args.command == "init":
            _report(init(root))
        elif args.command == "sync":
            _report(apply(root, load_config(root / CONFIG_FILE)))
        elif args.command == "size":
            return _size(args, root)
        elif args.command == "changelog":
            return _changelog(args, root)
        elif args.command == "changes":
            return _changes(args, root)
        elif args.command == "main-health":
            return _main_health(args)
        else:
            problems = diagnose(root)
            from devloop.council import warnings

            for warning in warnings(root):
                print(f"warning: {warning}")
            for problem in problems:
                print(f"problem: {problem}")
            print(f"{len(problems)} problem(s) found" if problems else "healthy")
            return 1 if problems and args.check else 0
    except (ConfigError, SyncError) as exc:
        print(f"devloop: {exc}", file=sys.stderr)
        return 2
    except UnicodeDecodeError as exc:
        print(f"devloop: a project file is not UTF-8 text: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"devloop: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
