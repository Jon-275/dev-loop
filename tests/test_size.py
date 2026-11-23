"""Tests for ``devloop size``: a PR's changed lines against ``pr.max_lines``."""

import email.message
import json
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import MergeRepo, lines

from devloop import github
from devloop.cli import main
from devloop.config import PR, Config, ExcludeBlock
from devloop.size import measure, run

CFG = replace(Config.default(version="0.1"), pr=PR(max_lines=100))
NO_API: dict[str, str] = {}


def _run(repo: Path, cfg: Config = CFG, event_path: str | None = None) -> tuple[int, list[str]]:
    return run("pull_request", event_path, repo, cfg, NO_API)


def _payload(tmp_path: Path, labels: list[str], number: int | None = 7) -> str:
    pr: dict[str, Any] = {"labels": [{"name": n} for n in labels]}
    if number is not None:
        pr["number"] = number
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"pull_request": pr}), encoding="utf-8")
    return str(path)


def test_a_pr_under_the_limit_passes_and_reports_its_size(merge_repo: MergeRepo) -> None:
    code, out = _run(merge_repo({"a.py": lines(40)}))
    assert code == 0
    assert out[0] == "Counted: 40 changed lines (added + deleted) in 1 files."
    assert out[-1] == "Within the limit: 40 of 100 lines."


def test_exactly_the_limit_passes(merge_repo: MergeRepo) -> None:
    assert _run(merge_repo({"a.py": lines(100)}))[0] == 0


def test_a_pr_over_the_limit_fails_and_says_how_to_split(
    merge_repo: MergeRepo, tmp_path: Path
) -> None:
    code, out = _run(merge_repo({"a.py": lines(101)}), event_path=_payload(tmp_path, []))
    assert code == 1
    assert "101 lines, over the 100-line limit" in out[-1]
    assert "large-pr-approved" in out[-1]


def test_deleted_lines_count_too(merge_repo: MergeRepo) -> None:
    repo = merge_repo({"a.py": ""}, base={"a.py": lines(60)})
    assert _run(repo)[1][0].startswith("Counted: 60 changed lines")


def test_the_largest_ten_files_are_listed_largest_first(merge_repo: MergeRepo) -> None:
    out = _run(merge_repo({f"f{n:02}.py": lines(n + 1) for n in range(12)}))[1]
    listed = [line.split()[-1] for line in out[2:12]]
    assert out[1] == "Largest 10 files:"
    assert listed == [f"f{n:02}.py" for n in range(11, 1, -1)]


def test_excluded_paths_are_named_but_not_counted(merge_repo: MergeRepo) -> None:
    cfg = replace(CFG, pr=PR(max_lines=100, exclude=("uv.lock", "data/*.json")))
    repo = merge_repo({"uv.lock": lines(500), "data/x/y.json": lines(300), "a.py": lines(5)})
    code, out = _run(repo, cfg)
    assert code == 0
    assert out[0].startswith("Counted: 5 changed lines")
    assert "Excluded: 800 lines in 2 files." in out
    assert any("uv.lock: matches pr.exclude 'uv.lock'" in line for line in out)


def test_skill_copies_written_by_sync_are_not_counted(merge_repo: MergeRepo) -> None:
    manifest = json.dumps({"files": [".claude/skills/s/SKILL.md"]})
    repo = merge_repo(
        {
            ".claude/skills/s/SKILL.md": lines(300),
            ".devloop/manifest.json": manifest,
            "devloop/skills/s/SKILL.md": lines(30),
        }
    )
    out = _run(repo)[1]
    assert out[0].startswith("Counted: 30 changed lines")
    assert any(".claude/skills/s/SKILL.md: written by devloop sync" in line for line in out)


def test_only_the_marked_block_of_a_file_is_left_out(merge_repo: MergeRepo) -> None:
    start, end = "<!-- gen:start -->", "<!-- gen:end -->"
    block = ExcludeBlock(path="AGENTS.md", start=start, end=end)
    cfg = replace(CFG, pr=PR(max_lines=100, exclude_blocks=(block,)))
    text = f"intro\n{lines(7, 'own')}{start}\n{lines(200, 'gen')}{end}\n"
    out = _run(merge_repo({"AGENTS.md": text}), cfg)[1]
    assert out[0].startswith("Counted: 8 changed lines")
    assert any("AGENTS.md: the marked block" in line for line in out)


def test_a_similar_path_outside_the_exclusions_counts(merge_repo: MergeRepo) -> None:
    cfg = replace(CFG, pr=PR(max_lines=100, exclude=("uv.lock",)))
    assert _run(merge_repo({"sub/uv.lock.py": lines(20)}), cfg)[1][0].startswith("Counted: 20")


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", ""])
def test_every_other_event_passes(merge_repo: MergeRepo, event: str) -> None:
    code, out = run(event, None, merge_repo({"a.py": lines(500)}), CFG, NO_API)
    assert code == 0
    assert "not a pull request" in out[0]


def test_a_head_that_is_not_a_merge_passes_with_a_note(tmp_path: Path) -> None:
    from conftest import git

    git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text(lines(500), encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    code, out = _run(tmp_path)
    assert code == 0
    assert "not a merge ref" in out[0]
    assert measure(tmp_path, CFG) is None


def test_the_label_approves_an_oversize_pr(merge_repo: MergeRepo, tmp_path: Path) -> None:
    payload = _payload(tmp_path, ["large-pr-approved"])
    code, out = _run(merge_repo({"a.py": lines(500)}), event_path=payload)
    assert code == 0
    assert "has the `large-pr-approved` label" in out[-1]
    assert "GitHub API read failed (no GITHUB_TOKEN)" in out[-2]


def test_another_label_does_not_approve(merge_repo: MergeRepo, tmp_path: Path) -> None:
    payload = _payload(tmp_path, ["bug"])
    assert _run(merge_repo({"a.py": lines(500)}), event_path=payload)[0] == 1


def test_unreadable_labels_pass_an_oversize_pr(merge_repo: MergeRepo, tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    code, out = _run(merge_repo({"a.py": lines(500)}), event_path=str(tmp_path / "bad.json"))
    assert code == 0
    assert "could not be read" in out[-1]


class FakeApi:
    """Answers label reads from a list of pages, recording each URL and token."""

    def __init__(self, pages: list[object] | Exception) -> None:
        """Answer with ``pages`` in order, or raise ``pages`` if it is an error."""
        self.pages = pages
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url: str, token: str) -> object:
        """Return the page ``url`` asks for, recording the call."""
        self.calls.append((url, token))
        if isinstance(self.pages, Exception):
            raise self.pages
        page = int(url.rsplit("page=", 1)[1])
        return self.pages[page - 1] if page <= len(self.pages) else []


ENV = {"GITHUB_TOKEN": "tok", "GITHUB_REPOSITORY": "o/r", "GITHUB_API_URL": "https://api.x"}


def _labels(
    tmp_path: Path, api: FakeApi, payload: list[str], env: dict[str, str] = ENV
) -> tuple[set[str] | None, str]:
    return github.current_labels(_payload(tmp_path, payload), env, fetch=api)


def test_a_label_added_after_the_push_counts(tmp_path: Path) -> None:
    names, source = _labels(tmp_path, FakeApi([[{"name": "large-pr-approved"}]]), [])
    assert names == {"large-pr-approved"}
    assert source == "Labels: read from the GitHub API when the check ran."


def test_a_label_removed_after_the_push_no_longer_counts(tmp_path: Path) -> None:
    names, _ = _labels(tmp_path, FakeApi([[]]), ["large-pr-approved"])
    assert names == set()


def test_the_token_goes_only_in_the_header_and_pages_are_followed(tmp_path: Path) -> None:
    full = [{"name": f"l{n}"} for n in range(100)]
    api = FakeApi([full, [{"name": "last"}]])
    names, _ = _labels(tmp_path, api, [])
    assert names is not None
    assert len(names) == 101
    assert [url for url, _ in api.calls] == [
        "https://api.x/repos/o/r/issues/7/labels?per_page=100&page=1",
        "https://api.x/repos/o/r/issues/7/labels?per_page=100&page=2",
    ]
    assert all(token == "tok" and "tok" not in url for url, token in api.calls)


def test_more_pages_than_the_cap_fall_back_to_the_payload(tmp_path: Path) -> None:
    full = [{"name": f"l{n}"} for n in range(100)]
    names, source = _labels(tmp_path, FakeApi([full] * 12), ["x"])
    assert names == {"x"}
    assert "more than 10 pages" in source


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (urllib.error.HTTPError("u", 403, "no", email.message.Message(), None), "HTTP 403"),
        (urllib.error.URLError("down"), "URLError"),
        (ValueError("bad json"), "ValueError"),
    ],
)
def test_a_failed_api_read_falls_back_and_says_why(
    tmp_path: Path, error: Exception, reason: str
) -> None:
    names, source = _labels(tmp_path, FakeApi(error), ["x"])
    assert names == {"x"}
    assert f"GitHub API read failed ({reason})" in source


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({**ENV, "GITHUB_TOKEN": ""}, "no GITHUB_TOKEN"),
        ({**ENV, "GITHUB_REPOSITORY": "../x"}, "no usable GITHUB_REPOSITORY"),
        ({**ENV, "GITHUB_API_URL": "http://api.x"}, "not https"),
    ],
)
def test_an_unusable_setting_falls_back_without_calling_the_api(
    tmp_path: Path, env: dict[str, str], reason: str
) -> None:
    api = FakeApi([[]])
    _, source = _labels(tmp_path, api, ["x"], env)
    assert api.calls == []
    assert reason in source


def test_a_non_list_answer_is_a_failed_read(tmp_path: Path) -> None:
    _, source = _labels(tmp_path, FakeApi([{"message": "no"}]), ["x"])
    assert "not a list of labels" in source


def test_the_real_request_keeps_the_token_off_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[]"

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> Response:
        seen.append(request)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert github.get_json("https://api.x/a", "tok") == []
    assert seen[0].unredirected_hdrs == {"Authorization": "Bearer tok"}
    assert "Authorization" not in seen[0].headers


def test_the_command_writes_the_step_summary(
    merge_repo: MergeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = merge_repo({"a.py": lines(5), "devloop.toml": '[framework]\nversion = "0.1"\n'})
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    assert main(["size", "--root", str(repo)]) == 0
    assert summary.read_text(encoding="utf-8").startswith("## PR size\n")


def test_a_manifest_entry_outside_the_skills_targets_is_still_counted(
    merge_repo: MergeRepo,
) -> None:
    manifest = json.dumps({"files": ["src/app.py", ".claude/skills/s/SKILL.md"]})
    repo = merge_repo({"src/app.py": lines(40), ".devloop/manifest.json": manifest})
    out = _run(repo)[1]
    assert out[0].startswith("Counted: 40 changed lines")


def test_retiring_a_skill_does_not_count_its_deleted_copies(merge_repo: MergeRepo) -> None:
    copies = [f"{t}/s/SKILL.md" for t in (".claude/skills", ".agents/skills", ".github/skills")]
    base = {path: lines(300) for path in copies}
    base["devloop/skills/s/SKILL.md"] = lines(300)
    base[".devloop/manifest.json"] = json.dumps({"files": copies})
    changed = {path: "" for path in [*copies, "devloop/skills/s/SKILL.md"]}
    changed[".devloop/manifest.json"] = json.dumps({"files": []})
    out = _run(merge_repo(changed, base=base))[1]
    assert out[0].startswith("Counted: 300 changed lines")


def _on_pr_branch(repo: Path) -> Path:
    """Check out the fixture's PR branch; ``main^1`` is main before it merged the PR."""
    from conftest import git

    git(repo, "checkout", "-q", "pr")
    return repo


def test_a_local_branch_is_measured_from_where_it_left_its_base(merge_repo: MergeRepo) -> None:
    """--base counts only the branch's own commits, with the same exclusions as CI."""
    repo = _on_pr_branch(merge_repo({"a.py": lines(40), "uv.lock": lines(500)}))
    cfg = replace(CFG, pr=PR(max_lines=100, exclude=("uv.lock",)))
    code, out = run("", None, repo, cfg, NO_API, base="main^1")
    assert code == 0
    assert out[0] == "Counted: 40 changed lines (added + deleted) in 1 files."
    assert "     500  uv.lock: matches pr.exclude 'uv.lock'" in out
    assert out[-1] == "Within the limit: 40 of 100 lines."


def test_a_local_branch_over_the_limit_exits_one_and_says_how_to_split(
    merge_repo: MergeRepo,
) -> None:
    repo = _on_pr_branch(merge_repo({"a.py": lines(101)}))
    code, out = run("", None, repo, CFG, NO_API, base="main^1")
    assert code == 1
    assert "101 lines, over the 100-line limit" in out[-1]
    assert "large-pr-approved" in out[-1]


def test_a_local_base_that_does_not_exist_is_an_error(merge_repo: MergeRepo) -> None:
    repo = _on_pr_branch(merge_repo({"a.py": lines(10)}))
    code, out = run("", None, repo, CFG, NO_API, base="no-such-branch")
    assert code == 2
    assert out == ["devloop size: cannot compare with no-such-branch; is it fetched?"]


def test_the_command_takes_a_local_base(
    merge_repo: MergeRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _on_pr_branch(merge_repo({"a.py": lines(10)}))
    (repo / "devloop.toml").write_text('[framework]\nversion = "0.5"\n', encoding="utf-8")
    assert main(["size", "--root", str(repo), "--base", "main^1"]) == 0
    assert "Counted: 10 changed lines" in capsys.readouterr().out
