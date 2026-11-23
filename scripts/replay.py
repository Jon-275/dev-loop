"""Replay one pull request through gtm_toolkit's reviewers and devloop's, and compare them.

Phase 3 of the devloop plan is done when gtm_toolkit's reviewer workflows are thin
callers of ``review.yml`` with "same findings on a replayed PR". This script checks that:

- the prompt each implementation sends must be identical, byte for byte: that part is
  deterministic, so any difference is a porting gap;
- the findings come from a model, so they are matched loosely (same severity and file,
  within five lines) and listed side by side.

Nothing is posted: both sides run with no known threads, no earlier review and a post
step that only records. Run it from a checkout of the pull request's head, with gtm's
review scripts and data copied from its ``main`` into ``--gtm`` (``scripts/``, ``data/``):

    python scripts/replay.py --gtm DIR --base SHA --providers gemini,reflection --out DIR

``--fake`` answers every request with an empty review and uses no key, so the prompt
comparison runs with no model at all. ``--public`` reports only counts and prompt hashes
and silences both sides' logs, for a public CI log replaying a private repository: no
prompt, diff, file name or finding title is written, and no JSON is kept.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import hashlib
import importlib.util
import io
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from devloop.config import parse_config
from devloop.review.gitread import run_git
from devloop.review.model import Effort, Settings, Usage
from devloop.review.providers import asker, provider_for, resolve
from devloop.review.run import Policy, run_review
from devloop.review.threads import load_feedback

FAKE_REPLY = json.dumps({"summary": "Fake.", "findings": [], "not_posted": []})
SCRIPTS = {"gemini": "gemini_pr_review", "reflection": "reflection_pr_review"}
ASKERS = {"gemini": "ask_gemini", "reflection": "ask_reflection"}
KEYS = {"gemini": "GEMINI_API_KEY", "reflection": "REFLECTION_API_KEY"}


@dataclass
class Side:
    """What one implementation asked and posted for one reviewer."""

    prompts: list[tuple[str, str]] = field(default_factory=list)
    body: str = ""
    comments: list[dict[str, Any]] = field(default_factory=list)
    log: str = ""
    error: str = ""


def _load(path: Path) -> ModuleType:
    """Import one of gtm_toolkit's review scripts by its path."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _recording(side: Side, ask: Callable[[str, Effort], str]) -> Callable[[str, Effort], str]:
    """Return ``ask``, recording each prompt and its effort in ``side``."""

    def record(prompt: str, effort: Effort) -> str:
        """Record the request and pass it on."""
        side.prompts.append((effort, prompt))
        return ask(prompt, effort)

    return record


def gtm_side(name: str, gtm: Path, base: str, head: str, key: str, fake: bool) -> Side:
    """Run gtm_toolkit's reviewer ``name`` over the pull request, posting nothing."""
    module = _load(gtm / "scripts" / f"{SCRIPTS[name]}.py")
    core = module.core
    side, usage = Side(), core.Usage()
    config = module.ReviewConfig(key, "", "replay/replay", "0", base, head)
    real = getattr(module, ASKERS[name])

    def ask(prompt: str, effort: Effort) -> str:
        """Send the prompt to the model, or answer it with an empty review."""
        return FAKE_REPLY if fake else str(real(key, prompt, effort, usage=usage))

    try:
        side.log = core.run_review(
            config,
            module.PROVIDER,
            ask=_recording(side, ask),
            known=lambda cfg: set(),
            post=lambda cfg, body, comments: _keep(side, body, comments),
            feedback=core.load_feedback(module.PROVIDER),
            previous=None,
            usage=usage,
        )
    except Exception as error:  # a replay reports every failure, by its type in public
        side.error = f"{type(error).__name__}: {error}"
    return side


def _keep(side: Side, body: str, comments: list[dict[str, Any]]) -> None:
    """Record what would have been posted."""
    side.body, side.comments = body, list(comments)


def devloop_toml(gtm: Path) -> str:
    """Return a ``devloop.toml`` that sets devloop up as gtm_toolkit's reviewers are."""
    core = _load(gtm / "scripts" / "review_core.py")
    reflection = _load(gtm / "scripts" / "reflection_pr_review.py")
    files = ", ".join(json.dumps(p) for p in core.REVIEWED_PATHSPECS)
    effort = ", ".join(json.dumps(f"*{hint}*") for hint in core.CORE_LOGIC_HINTS)
    dims = ", ".join(f"{tag} = {json.dumps(text)}" for tag, text in reflection.DIMENSIONS.items())
    return f"""[framework]
version = "0.3"

[review]
files = [{files}]
high_effort = [{effort}]
blocking_severities = ["P1", "P2", "P3"]
lenses_dir = "lenses"
feedback = "data/review_feedback.json"
lessons = "data/review_lessons.json"

[review.reviewers.reflection]
dimensions = {{ {dims} }}
summary = {json.dumps(reflection.SUMMARY_RULE)}
"""


def write_trusted(gtm: Path, trusted: Path) -> None:
    """Write devloop's trusted side: the config, gtm's lenses, and gtm's review data."""
    (trusted / "lenses").mkdir(parents=True, exist_ok=True)
    (trusted / "data").mkdir(exist_ok=True)
    (trusted / "devloop.toml").write_text(devloop_toml(gtm), encoding="utf-8")
    for name, script in SCRIPTS.items():
        lens = _load(gtm / "scripts" / f"{script}.py").LENS
        (trusted / "lenses" / f"{name}.md").write_text(lens, encoding="utf-8")
    for data in ("review_feedback.json", "review_lessons.json"):
        source = gtm / "data" / data
        if source.is_file():
            (trusted / "data" / data).write_bytes(source.read_bytes())


def devloop_side(name: str, trusted: Path, base: str, head: str, key: str, fake: bool) -> Side:
    """Run devloop's reviewer ``name`` over the pull request, posting nothing."""
    config = parse_config((trusted / "devloop.toml").read_text(encoding="utf-8"))
    review = config.review
    reviewer = resolve(name, review.reviewers)
    provider = provider_for(reviewer, (trusted / "lenses" / f"{name}.md").read_text())
    side, usage = Side(), Usage()
    real = asker(reviewer, provider, key, usage)

    def ask(prompt: str, effort: Effort) -> str:
        """Send the prompt to the model, or answer it with an empty review."""
        return FAKE_REPLY if fake else real(prompt, effort)

    policy = Policy(
        files=review.files, high_effort=review.high_effort, blocking=review.blocking_severities
    )
    try:
        side.log = run_review(
            Settings(key, "", "", "", base, head),
            provider,
            policy,
            name=name,
            ask=_recording(side, ask),
            known=set,
            post=lambda body, comments: _keep(side, body, comments),
            feedback=load_feedback(provider, trusted / review.feedback, trusted / review.lessons),
            usage=usage,
        )
    except Exception as error:  # a replay reports every failure, by its type in public
        side.error = f"{type(error).__name__}: {error}"
    return side


def findings(side: Side) -> list[tuple[str, str, int, str]]:
    """Return the findings a side posted as threads: severity, path, line and title."""
    found = []
    for comment in side.comments:
        body, line, path = str(comment.get("body") or ""), comment.get("line"), comment.get("path")
        if not body or not isinstance(line, int) or not isinstance(path, str) or not path:
            continue
        heading = body.splitlines()[0]
        severity, _, title = heading.strip("*").partition(" — ")
        found.append((severity, path, line, title))
    return sorted(found)


def matched(one: list[tuple[str, str, int, str]], other: list[tuple[str, str, int, str]]) -> int:
    """Return how many findings of ``one`` have a finding of ``other`` nearby (same kind)."""
    return sum(
        any(o[0] == f[0] and o[1] == f[1] and abs(o[2] - f[2]) <= 5 for o in other) for f in one
    )


def _digest(side: Side) -> str:
    """Return a short hash of every prompt a side sent, with its effort."""
    joined = "\0".join(f"{effort}\n{prompt}" for effort, prompt in side.prompts)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def compare(name: str, gtm: Side, dev: Side, public: bool = False) -> tuple[bool, list[str]]:
    """Return whether the prompts are identical, and the report's lines for ``name``.

    A ``public`` report holds counts and hashes only.
    """
    same = [g == d for g, d in zip(gtm.prompts, dev.prompts, strict=False)]
    identical = len(gtm.prompts) == len(dev.prompts) and all(same)
    lines = [f"## {name}", ""]
    lines.append(
        f"- Prompts: {len(gtm.prompts)} from gtm_toolkit, {len(dev.prompts)} from devloop; "
        + ("**identical**." if identical else "**different**.")
    )
    lines.append(f"- Prompt hashes: gtm_toolkit `{_digest(gtm)}`, devloop `{_digest(dev)}`.")
    for label, side in (("gtm_toolkit", gtm), ("devloop", dev)):
        if side.error:
            shown = side.error.split(":", 1)[0] if public else side.error[:300]
            lines.append(f"- {label} failed: `{shown}`")
        else:
            lines.append(f"- {label}: " + ("completed." if public else side.log))
    if not identical and not public:
        g = gtm.prompts[0] if gtm.prompts else ("", "")
        d = dev.prompts[0] if dev.prompts else ("", "")
        diff = difflib.unified_diff(
            f"effort: {g[0]}\n{g[1]}".splitlines(),
            f"effort: {d[0]}\n{d[1]}".splitlines(),
            "gtm_toolkit",
            "devloop",
            lineterm="",
            n=1,
        )
        lines += [
            "",
            "First prompt, gtm_toolkit to devloop:",
            "",
            "```diff",
            *list(diff)[:120],
            "```",
        ]
    ours, theirs = findings(gtm), findings(dev)
    lines += [
        "",
        f"- Findings posted as threads: gtm_toolkit {len(ours)}, devloop {len(theirs)}; "
        f"{matched(ours, theirs)} of gtm_toolkit's have a devloop finding of the same severity "
        f"and file within five lines, and {matched(theirs, ours)} of devloop's have one of "
        "gtm_toolkit's.",
        "",
    ]
    if public:
        return identical, lines
    lines += [
        "| Side | Severity | Place | Title |",
        "|---|---|---|---|",
        *(f"| gtm_toolkit | {s} | `{p}:{n}` | {t} |" for s, p, n, t in ours),
        *(f"| devloop | {s} | `{p}:{n}` | {t} |" for s, p, n, t in theirs),
        "",
    ]
    return identical, lines


def main(argv: list[str] | None = None) -> int:
    """Replay the pull request; return 0 when every prompt matched and no side failed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gtm", type=Path, required=True, help="gtm_toolkit's scripts/ and data/")
    parser.add_argument("--base", required=True, help="the pull request's base commit")
    parser.add_argument("--providers", default="gemini,reflection")
    parser.add_argument("--out", type=Path, required=True, help="where the report goes")
    parser.add_argument("--fake", action="store_true", help="no model: compare prompts only")
    parser.add_argument("--public", action="store_true", help="counts and hashes only")
    args = parser.parse_args(argv)
    names = [n.strip() for n in args.providers.split(",") if n.strip()]
    unknown = sorted(set(names) - set(SCRIPTS))
    if unknown or not names:
        parser.error(f"--providers: choose from {', '.join(SCRIPTS)}; not {', '.join(unknown)}")
    head = run_git(["rev-parse", "HEAD"]).strip()
    base = run_git(["rev-parse", args.base]).strip()
    args.out.mkdir(parents=True, exist_ok=True)
    trusted = args.out / "trusted"
    write_trusted(args.gtm.resolve(), trusted)
    report = [
        "# Replay of gtm_toolkit's reviewers through devloop",
        "",
        f"Base `{base}`, head `{head}`.",
    ]
    if args.fake:
        report.append("Fake model: prompts only.")
    report.append("")
    ok, raw = True, {}
    for name in names:
        key = "fake" if args.fake else os.environ.get(KEYS[name], "")
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet if args.public else sys.stdout):
            with contextlib.redirect_stderr(quiet if args.public else sys.stderr):
                gtm = gtm_side(name, args.gtm.resolve(), base, head, key, args.fake)
                dev = devloop_side(name, trusted, base, head, key, args.fake)
        identical, lines = compare(name, gtm, dev, args.public)
        ok &= identical and not gtm.error and not dev.error
        report += lines
        raw[name] = {"gtm_toolkit": gtm.__dict__, "devloop": dev.__dict__}
    (args.out / "replay.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    if not args.public:
        (args.out / "replay.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")
    print("\n".join(report))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
