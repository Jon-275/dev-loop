"""The ``devloop`` command line.

Exit codes: 0 success (or a report without ``--check``), 1 doctor found problems,
2 the command could not run (bad configuration or sources).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from devloop import changelog, changes, pr_open
from devloop.config import CONFIG_FILE, ConfigError, load_config
from devloop.doctor import diagnose
from devloop.init import init
from devloop.size import run as run_size
from devloop.sync import Change, SyncError, apply


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
        ("size", "in CI, report a pull request's size and fail one over pr.max_lines"),
        ("changelog", "check changelog fragments, or build them into the changelog"),
        ("changes", "in CI, say whether a pull request changes only documentation"),
        ("pr-open", "refuse a push to a branch whose PR already merged or closed"),
    ):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--root", type=Path, default=Path.cwd(), help="project root")
        if name == "doctor":
            sub.add_argument("--check", action="store_true", help="exit 1 on any problem")
        if name in ("size", "changes"):
            event = os.environ.get("GITHUB_EVENT_NAME", "")
            sub.add_argument("--event", default=event, help="github.event_name")
        if name == "changelog":
            actions = sub.add_subparsers(dest="action", required=True)
            check = actions.add_parser("check", help="validate the fragments")
            check.add_argument("--pr", action="store_true", help="note code with no entry")
            build = actions.add_parser("build", help="move the fragments into the changelog")
            build.add_argument("--dry-run", action="store_true", help="print, write nothing")
    return parser


def _changes(args: argparse.Namespace, root: Path) -> int:
    """Write ``code`` and ``doc_tests`` for GitHub Actions; always exits 0."""
    config = load_config(root / CONFIG_FILE)
    docs = config.docs
    skills = (config.skills.project, *config.agents.skills_targets)
    code, files = changes.decide(args.event, root, docs, skills)
    tests = [] if code else changes.doc_tests(root, docs)
    if code:
        print("Code changed (or the change could not be read): every check runs.")
    else:
        print(f"Only documentation changed ({len(files)} files): {', '.join(files)}")
        print(f"The {len(tests)} test files that read documents still run.")
    lines = [f"code={'true' if code else 'false'}", f"doc_tests={' '.join(tests)}"]
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))
    return 0


def _changelog(args: argparse.Namespace, root: Path) -> int:
    """Check or build the changelog fragments; return 1 only for invalid fragments."""
    log = load_config(root / CONFIG_FILE).changelog
    if args.action == "build":
        try:
            result = changelog.build(root, log, dry_run=args.dry_run)
        except ValueError as exc:
            print(f"devloop changelog: {exc}", file=sys.stderr)
            return 1
        if args.dry_run:
            print(result, end="")
        return 0
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
    config = load_config(root / CONFIG_FILE)
    code, lines = run_size(
        args.event, os.environ.get("GITHUB_EVENT_PATH"), root, config, os.environ
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
    args = _parser().parse_args(argv)
    root: Path = args.root.resolve()
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
        else:
            problems = diagnose(root)
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
