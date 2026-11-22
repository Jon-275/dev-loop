"""The ``devloop`` command line.

Exit codes: 0 success (or a report without ``--check``), 1 doctor found problems,
2 the command could not run (bad configuration or sources).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from devloop.config import CONFIG_FILE, ConfigError, load_config
from devloop.doctor import diagnose
from devloop.init import init
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
    ):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--root", type=Path, default=Path.cwd(), help="project root")
        if name == "doctor":
            sub.add_argument("--check", action="store_true", help="exit 1 on any problem")
    return parser


def _report(changes: list[Change]) -> None:
    """Print each change, or that there was nothing to do."""
    for change in changes:
        print(f"{change.action} {change.path}")
    if not changes:
        print("up to date")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return its exit code."""
    args = _parser().parse_args(argv)
    root: Path = args.root.resolve()
    try:
        if args.command == "init":
            _report(init(root))
        elif args.command == "sync":
            _report(apply(root, load_config(root / CONFIG_FILE)))
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
