"""Incremental reviews on real temporary repositories: what is reviewed after a review."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from review_helpers import run

from devloop.config import REVIEWED_FILES
from devloop.review import increment as inc
from devloop.review.gitread import changed_files, run_git_diff
from devloop.review.model import Baseline, ReviewError

WHO = ("-c", "user.name=t", "-c", "user.email=t@example.invalid")


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    """Write ``files``, commit them and return the new SHA."""
    for name, text in files.items():
        (repo / name).write_text(text)
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", message)
    return run(repo, "rev-parse", "HEAD")


def _conflicted_merge(repo: Path) -> None:
    """Merge ``main`` into ``pr`` and check it stopped on a conflict."""
    done = subprocess.run(["git", *WHO, "merge", "-q", "main"], cwd=repo, capture_output=True)
    assert done.returncode == 1 and (repo / ".git" / "MERGE_HEAD").exists(), done.stderr


def _merge_main(repo: Path, extra: dict[str, str] | None = None) -> str:
    """Merge ``main`` into ``pr`` (adding ``extra`` to the merge commit); return the head."""
    run(repo, "merge", "-q", "--no-ff", "--no-commit", "main")
    return _commit(repo, extra or {}, "merge main")


@pytest.fixture()
def pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return ``main`` with three files and branch ``pr`` (checked out) changing ``app.py``.

    ``main`` then moves on with a change to ``other.py``.
    """
    root = tmp_path / "repo"
    root.mkdir()
    run(root, "init", "-q", "-b", "main")
    _commit(root, {"app.py": "a = 1\n", "lib.py": "b = 1\n", "other.py": "c = 1\n"}, "base")
    run(root, "checkout", "-q", "-b", "pr")
    _commit(root, {"app.py": "a = 2\n"}, "reviewed")
    run(root, "checkout", "-q", "main")
    _commit(root, {"other.py": "c = 2\n"}, "main moves on")
    run(root, "checkout", "-q", "pr")
    monkeypatch.chdir(root)
    return root


def _plan(repo: Path, baseline: Baseline | None) -> inc.Plan:
    """Plan the review of ``pr``'s head against ``main`` after ``baseline``."""
    base, head = run(repo, "rev-parse", "main"), run(repo, "rev-parse", "pr")
    changed = changed_files(base, head, REVIEWED_FILES)
    full = run_git_diff([f"{base}...{head}", "--", *REVIEWED_FILES])
    return inc.plan(baseline, base, head, REVIEWED_FILES, full, changed)


def _since(repo: Path, reviewed: str, back: int = 1) -> Baseline:
    """Return the baseline of a review of ``reviewed`` against ``main~back``."""
    return Baseline(reviewed, run(repo, "rev-parse", f"main~{back}"))


def test_a_clean_merge_of_main_is_not_reviewed_again(pr: Path) -> None:
    reviewed = run(pr, "rev-parse", "pr")
    head = _merge_main(pr)
    assert inc.merge_only(reviewed, head, run(pr, "rev-parse", "main"))
    assert not inc.merge_only(head, head, run(pr, "rev-parse", "main"))
    plan = _plan(pr, _since(pr, reviewed))
    assert (
        plan.nothing_new == f"No new changes since {reviewed[:12]}; merge of the base branch only"
    )


def test_a_merge_that_resolves_a_conflict_is_reviewed_in_full(pr: Path) -> None:
    reviewed = run(pr, "rev-parse", "pr")
    run(pr, "checkout", "-q", "main")
    _commit(pr, {"app.py": "a = 3\n"}, "main edits the same line")
    run(pr, "checkout", "-q", "pr")
    _conflicted_merge(pr)
    _commit(pr, {"app.py": "a = 4\n"}, "resolve the conflict")
    plan = _plan(pr, _since(pr, reviewed, 2))
    assert plan.increment is None and "+a = 4" in plan.diff


def test_merge_attributes_in_the_pull_request_cannot_hide_a_conflict(pr: Path) -> None:
    _commit(pr, {".gitattributes": "*.py merge=union\n"}, "union merges")
    reviewed = run(pr, "rev-parse", "pr")
    run(pr, "checkout", "-q", "main")
    _commit(pr, {"app.py": "a = 3\n"}, "main edits the same line")
    run(pr, "checkout", "-q", "pr")
    head = _merge_main(pr)
    assert not inc.merge_only(reviewed, head, run(pr, "rev-parse", "main"))


def test_a_clean_merge_of_another_branch_is_not_merge_only(pr: Path) -> None:
    reviewed = run(pr, "rev-parse", "pr")
    run(pr, "checkout", "-q", "-b", "side", "main~1")
    _commit(pr, {"lib.py": "b = 9\n"}, "unreviewed work")
    run(pr, "checkout", "-q", "pr")
    run(pr, "merge", "-q", "--no-ff", "--no-edit", "side")
    assert not inc.merge_only(reviewed, run(pr, "rev-parse", "pr"), run(pr, "rev-parse", "main"))
    assert "+b = 9" in _plan(pr, _since(pr, reviewed)).diff


def test_new_commits_and_a_merge_review_only_the_pull_requests_own_changes(pr: Path) -> None:
    reviewed = run(pr, "rev-parse", "pr")
    _commit(pr, {"lib.py": "b = 3\n"}, "more work")
    _merge_main(pr, {"lib.py": "b = 4\n"})
    plan = _plan(pr, _since(pr, reviewed))
    assert "+b = 4" in plan.diff and "other.py" not in plan.diff and "+a = 2" not in plan.diff
    assert plan.paths == ["lib.py"] and plan.increment is not None
    assert plan.increment.carried == (("app.py", 1),)
    assert f"since commit {reviewed[:12]}" in inc.increment_note(plan.increment)


def test_a_conflict_only_in_an_unreviewed_file_keeps_the_review_incremental(pr: Path) -> None:
    _commit(pr, {"uv.lock": "pr\n", "extra.py": "e = 1\n"}, "lock")
    reviewed = run(pr, "rev-parse", "pr")
    run(pr, "checkout", "-q", "main")
    _commit(pr, {"uv.lock": "main\n"}, "main locks")
    run(pr, "checkout", "-q", "pr")
    _conflicted_merge(pr)
    (pr / "extra.py").unlink()
    _commit(pr, {"uv.lock": "both\n", "lib.py": "b = 4\n"}, "resolve")
    plan = _plan(pr, _since(pr, reviewed, 2))
    assert plan.increment is not None and plan.increment.restored == ("extra.py",)
    assert "+b = 4" in plan.diff and "uv.lock" not in plan.diff


def test_a_change_to_unreviewed_files_only_needs_no_review(pr: Path) -> None:
    reviewed = run(pr, "rev-parse", "pr")
    _commit(pr, {"README.md": "docs\n"}, "docs")
    plan = _plan(pr, _since(pr, reviewed))
    assert plan.nothing_new == f"No reviewed changes since {reviewed[:12]}; nothing new to review"


def test_a_git_failure_is_an_error_not_a_no(pr: Path) -> None:
    with pytest.raises(ReviewError, match="merge-base failed"):
        inc.is_ancestor(run(pr, "rev-parse", "pr"), "not-a-commit")


def test_file_names_from_the_pull_request_are_never_pathspec_magic(pr: Path) -> None:
    reviewed = run(pr, "rev-parse", "pr")
    _commit(pr, {"lib.py": "import os; os.system('evil')\n", ":(exclude)*.py": "x\n"}, "e")
    assert "os.system('evil')" in _plan(pr, _since(pr, reviewed)).diff


def test_a_rename_is_sent_and_counted_as_its_deletion_and_its_addition(pr: Path) -> None:
    reviewed = run(pr, "rev-parse", "pr")
    run(pr, "mv", "lib.py", "lib2.py")
    run(pr, "commit", "-q", "-m", "rename")
    plan = _plan(pr, _since(pr, reviewed))
    assert "deleted file mode" in plan.diff and "new file mode" in plan.diff
    assert "rename from" not in plan.diff
    assert plan.increment is not None and (plan.increment.hunks, plan.increment.files) == (2, 2)


def test_git_decides_which_conflicted_files_are_reviewed(pr: Path) -> None:
    (pr / "Dockerfile").mkdir()
    reviewed = _commit(pr, {"Dockerfile/notes.txt": "pr\n"}, "notes")
    run(pr, "checkout", "-q", "main")
    (pr / "Dockerfile").mkdir()
    _commit(pr, {"Dockerfile/notes.txt": "main\n"}, "main notes")
    run(pr, "checkout", "-q", "pr")
    _conflicted_merge(pr)
    _commit(pr, {"Dockerfile/notes.txt": "both\n", "lib.py": "b = 6\n"}, "resolve")
    plan = _plan(pr, _since(pr, reviewed, 2))
    assert plan.increment is None and "+a = 2" in plan.diff


@pytest.mark.parametrize("case", ["none", "force-push", "missing", "retargeted", "same-head"])
def test_anything_else_gets_a_full_review(pr: Path, case: str) -> None:
    reviewed = run(pr, "rev-parse", "pr")
    old_base = run(pr, "rev-parse", "main~1")
    if case == "force-push":
        run(pr, "reset", "-q", "--hard", "main~1")
        _commit(pr, {"app.py": "a = 5\n", "lib.py": "b = 5\n"}, "rewritten")
    elif case == "retargeted":
        run(pr, "checkout", "-q", "-b", "elsewhere", "main~1")
        old_base = _commit(pr, {"README.md": "x\n"}, "another base")
        run(pr, "checkout", "-q", "pr")
        _commit(pr, {"lib.py": "b = 5\n"}, "more work")
    elif case != "same-head":
        _commit(pr, {"lib.py": "b = 5\n"}, "more work")
    if case == "missing":
        reviewed = "1" * 40
    plan = _plan(pr, None if case == "none" else Baseline(reviewed, old_base))
    assert plan.increment is None and not plan.nothing_new and "app.py" in plan.diff
