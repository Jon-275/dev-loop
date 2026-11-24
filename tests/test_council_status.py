"""Tests for posting the Council review status (devloop.council_status).

The invariant: the status on a pull request's head is the verdict of a report on that
head, or pending while a required tier has none, or success where no council is needed.
CI never overwrites a verdict a maintainer posted on the same head, and a read that
failed posts nothing.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from devloop.config import Config
from devloop.council import parse_card
from devloop.council_report import parse_report
from devloop.council_status import CONTEXT, Api, publish
from devloop.review import feedback as fb
from devloop.review.github import Reply

HEAD = "a" * 40
CARD = (
    '---\nname: architect\ntitle: Architect\nsummoned_by: ["**"]\ntiers: [1, 2]\n'
    "reviewer: core\n---\nMandate: m\nChecks: c\nSeverity anchors: s\nProof methods: p\n"
)
CARDS = {"architect": parse_card(CARD, "architect")}
CONFIG = Config.default(version="0.6")
TIERED = replace(CONFIG, tiers={1: ("src/*",), 3: ("docs/*",)})


def _report(pr: int = 7, head: str = HEAD, verdict: str = "safe") -> str:
    """Return a report's text."""
    return (
        f'---\npr: {pr}\nhead: "{head}"\ntier: 1\nreviewers: ["core"]\n'
        f'verdict: "{verdict}"\nfindings: {{"P0": 0, "P1": 0, "P2": 0, "P3": 0}}\n---\n'
    )


class Fake:
    """A fake GitHub with one pull request (#7); records every status posted."""

    def __init__(
        self,
        files: list[str],
        current: str | None = None,
        fail: str = "",
        pages: int = 1,
        said: str = "safe: P0 0, P1 0, P2 0, P3 0",
        renamed: dict[str, str] | None = None,
    ) -> None:
        """Answer with ``files`` over ``pages`` pages, ``current`` as the head's status."""
        self.files, self.current, self.fail, self.pages = files, current, fail, pages
        self.said, self.renamed = said, renamed or {}
        self.posted: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, token: str, body: object) -> Reply:
        """Answer one request."""
        if self.fail and self.fail in url:
            return Reply(403, {"message": "no"})
        if method == "POST":
            assert isinstance(body, dict) and url.endswith(f"/statuses/{HEAD}")
            self.posted.append(body)
            return Reply(201, {})
        if url.endswith("/pulls/7"):
            return Reply(200, {"head": {"sha": HEAD}})
        if "/pulls/7/files" in url:
            asked = re.search(r"[?&]page=(\d+)", url)
            page = int(asked.group(1)) if asked else 1
            share = [f for i, f in enumerate(self.files) if i % self.pages == page - 1]
            more = f"https://api.github.com/repos/o/r/pulls/7/files?page={page + 1}"
            listed = [
                {
                    "filename": f,
                    **({"previous_filename": self.renamed[f]} if f in self.renamed else {}),
                }
                for f in share
            ]
            return Reply(200, listed, more if page < self.pages else None)
        if f"/commits/{HEAD}/status" in url:
            status = {"context": CONTEXT, "state": self.current, "description": self.said}
            found = [status] if self.current else []
            return Reply(200, {"statuses": found})
        raise AssertionError(f"unexpected {method} {url}")


def _publish(fake: Fake, text: str | None, config: Config = TIERED) -> str:
    """Publish for #7 against ``fake`` and return the state it decided."""
    api = Api("t", "o/r", send=fake, sleep=lambda _: None)
    report = parse_report(text) if text is not None else None
    return publish(api, 7, CARDS, config, report, "run").state


def test_a_safe_report_on_the_head_is_posted_as_success() -> None:
    """The maintainer's report becomes the head's status."""
    fake = Fake(["src/app.py"])
    assert _publish(fake, _report()) == "success"
    assert [(p["state"], p["context"]) for p in fake.posted] == [("success", CONTEXT)]
    assert fake.posted[0]["target_url"] == "run"


def test_ci_marks_a_required_tier_pending_and_a_tier_without_one_passed() -> None:
    """With no report, a tier-1 head waits; a docs-only head needs no council."""
    tier1, tier3 = Fake(["src/app.py"]), Fake(["docs/a.md"])
    assert _publish(tier1, None) == "pending"
    assert _publish(tier3, None) == "success"
    assert "not required" in tier3.posted[0]["description"]


@pytest.mark.parametrize("existing", ["success", "failure"])
def test_ci_never_overwrites_a_verdict_already_on_the_head(existing: str) -> None:
    """A maintainer's verdict on this exact head stands; CI posts nothing over it."""
    fake = Fake(["src/app.py"], current=existing)
    assert _publish(fake, None) == existing
    assert fake.posted == []


def test_ci_replaces_its_own_pending_and_a_report_replaces_anything() -> None:
    """Pending is CI's own; a maintainer's report is posted whatever stands."""
    pending = Fake(["src/app.py"], current="pending")
    assert _publish(pending, None) == "pending" and len(pending.posted) == 1
    old = Fake(["src/app.py"], current="success")
    assert _publish(old, _report(verdict="not safe")) == "failure"
    assert old.posted[0]["state"] == "failure"


def test_a_report_for_another_pull_request_or_commit_is_posted_as_pending() -> None:
    """The status says why it is not judged."""
    other_pr, old_head = Fake(["src/app.py"]), Fake(["src/app.py"])
    assert _publish(other_pr, _report(pr=8)) == "pending"
    assert _publish(old_head, _report(head="b" * 40)) == "pending"
    assert "#8" in other_pr.posted[0]["description"]


def test_the_change_is_read_from_every_page_of_its_files() -> None:
    """A tier-1 file on a later page still makes the change tier 1."""
    files = [f"docs/{n}.md" for n in range(5)] + ["src/app.py"]
    assert _publish(Fake(files, pages=3), None) == "pending"


@pytest.mark.parametrize("part", ["/pulls/7/files", "/commits/", "/pulls/7"])
def test_a_failed_read_posts_nothing(part: str) -> None:
    """Not judged stays not judged: nothing is posted from a read that failed."""
    fake = Fake(["src/app.py"], fail=part)
    with pytest.raises(fb.FeedbackError):
        _publish(fake, None)
    assert fake.posted == []


def test_posting_needs_the_pull_request_and_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--post`` without ``--pr``, or with no token and no gh, cannot run (exit 2)."""
    from devloop.council import main

    root = tmp_path / "repo"
    (root / "devloop" / "council").mkdir(parents=True)
    (root / "devloop.toml").write_text('[framework]\nversion = "0.6"\n', encoding="utf-8")
    (root / "devloop" / "council" / "architect.md").write_text(CARD, encoding="utf-8")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "no-gh"))
    base = ["report", "--root", str(root), "--post"]
    assert main(base) == 2
    assert "--post needs --pr" in capsys.readouterr().err
    assert main([*base, "--pr", "7", "--repo", "o/r"]) == 2
    assert "GITHUB_TOKEN" in capsys.readouterr().err
    assert main(["report", "--root", str(root), "--paths", "a.py"]) == 2
    assert "--report is needed" in capsys.readouterr().err


def test_a_failed_github_read_fails_the_run_so_it_is_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing posted is exit 2, never the exit 1 that pending gives."""
    from devloop import council_status
    from devloop.council import main

    def broken(*args: object, **kwargs: object) -> None:
        raise fb.FeedbackError("GitHub said 502")

    root = tmp_path / "repo"
    (root / "devloop" / "council").mkdir(parents=True)
    (root / "devloop.toml").write_text('[framework]\nversion = "0.6"\n', encoding="utf-8")
    (root / "devloop" / "council" / "architect.md").write_text(CARD, encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(council_status, "publish", broken)
    argv = ["report", "--root", str(root), "--post", "--pr", "7", "--repo", "o/r"]
    assert main(argv) == 2


def test_ci_re_judges_its_own_not_required_pass() -> None:
    """CI's own "not required" success is not a council verdict, so it is never kept."""
    said = "tier 2: council review not required"
    fake = Fake(["src/app.py"], current="success", said=said)
    assert _publish(fake, None) == "pending"
    assert fake.posted[0]["state"] == "pending"


def test_a_renamed_file_is_tiered_by_where_it_came_from_too() -> None:
    """Moving tier-1 code out of a tier-1 path keeps the change tier 1."""
    fake = Fake(["docs/payments.md"], renamed={"docs/payments.md": "src/payments.py"})
    assert _publish(fake, None) == "pending"
