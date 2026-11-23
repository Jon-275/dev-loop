"""Reading the pull request through Git only: file lists, contents and diff parsing."""

import subprocess
from pathlib import Path

import pytest
from review_helpers import commit, run, section

from devloop.config import REVIEWED_FILES
from devloop.review import gitread

DIFF = """diff --git a/app.py b/app.py
index 1..2 100644
--- a/app.py
+++ b/app.py
@@ -10,3 +10,4 @@ def f():
 a = 1
--- b = 2
+++ b = 3
+c = 4
 d = 5
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x = 1
-y = 2
"""


def test_deleted_files_are_reviewed_from_the_diff(repo: Path) -> None:
    base = run(repo, "rev-parse", "HEAD")
    (repo / "gone.py").unlink()
    head = commit(repo, "delete")
    changed = gitread.changed_files(base, head, REVIEWED_FILES)
    assert changed == ["gone.py"]
    assert gitread.reviewable_blobs(head, changed) == []


def test_symlinks_are_never_read(repo: Path) -> None:
    base = run(repo, "rev-parse", "HEAD")
    (repo / "aaa_env.py").symlink_to("/proc/self/environ")
    (repo / "keep.py").write_text("x = 3\n")
    head = commit(repo, "symlink")
    changed = gitread.changed_files(base, head, REVIEWED_FILES)
    assert sorted(changed) == ["aaa_env.py", "keep.py"]
    assert gitread.reviewable_blobs(head, changed) == ["keep.py"]


def test_contents_come_from_the_head_commit_not_the_working_tree(repo: Path) -> None:
    (repo / "keep.py").write_text("x = 42\n")
    head = commit(repo, "edit")
    (repo / "keep.py").write_text("SECRET = 'working tree'\n")
    assert gitread.file_contents(head, "keep.py") == "x = 42\n"


def test_only_the_configured_file_types_are_reviewed(repo: Path) -> None:
    base = run(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("changed\n")
    (repo / ".github").mkdir()
    (repo / ".github" / "ci.yml").write_text("on: push\n")
    (repo / "Dockerfile").write_text("FROM python\n")
    head = commit(repo, "config")
    assert sorted(gitread.changed_files(base, head, REVIEWED_FILES)) == [
        ".github/ci.yml",
        "Dockerfile",
    ]
    assert gitread.changed_files(base, head, ("*.md",)) == ["README.md"]


def test_changed_files_outside_the_diff_are_listed_as_unshown(repo: Path) -> None:
    base = run(repo, "rev-parse", "HEAD")
    (repo / "pyproject.toml").write_text("[project]\n")
    (repo / "uv.lock").write_text("version = 1\n")
    (repo / "README.md").write_text("changed\n")
    head = commit(repo, "add a dependency")
    changed = gitread.changed_files(base, head, REVIEWED_FILES)
    assert changed == ["pyproject.toml"]
    assert sorted(gitread.unshown_files(base, head, changed)) == ["README.md", "uv.lock"]


def test_an_attributes_file_cannot_run_a_diff_program(repo: Path) -> None:
    base = run(repo, "rev-parse", "HEAD")
    (repo / ".gitattributes").write_text("*.py diff=evil\n")
    (repo / "keep.py").write_text("x = 2\n")
    head = commit(repo, "attributes")
    run(repo, "config", "diff.evil.command", f"touch {repo / 'pwned'}")
    run(repo, "config", "diff.evil.textconv", f"touch {repo / 'pwned'}; cat")
    assert "+x = 2" in gitread.run_git_diff([f"{base}...{head}", "--", "*.py"])
    assert not (repo / "pwned").exists()


def test_run_git_ignores_an_inherited_git_dir(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decoy = tmp_path / "decoy"
    subprocess.run(["git", "init", "-q", str(decoy)], check=True, capture_output=True)
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / ".git" / "index"))
    assert sorted(gitread.run_git(["ls-files"]).split()) == ["README.md", "gone.py", "keep.py"]


def test_diff_lines_reads_hunks_by_their_counts() -> None:
    lines = gitread.diff_lines(DIFF)
    assert set(lines) == {"app.py", "gone.py"}
    assert lines["app.py"].right == {10, 11, 12, 13}
    assert lines["app.py"].left == {11}
    assert lines["gone.py"].right == set() and lines["gone.py"].left == {1, 2}


def test_quoted_paths_in_diff_headers_are_decoded() -> None:
    diff = (
        'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
        '--- "a/caf\\303\\251.py"\n'
        '+++ "b/caf\\303\\251.py"\n'
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    assert set(gitread.diff_lines(diff)) == {"café.py"}
    assert gitread.unquote_git_path('"b/tab\\there \\"q\\".py"') == 'b/tab\there "q".py'
    assert gitread.unquote_git_path("b/plain.py") == "b/plain.py"


def test_quoted_and_spaced_paths_from_git_itself_are_decoded(repo: Path) -> None:
    base = run(repo, "rev-parse", "HEAD")
    (repo / "café.py").write_text("x = 1\n")
    (repo / "my module.py").write_text("x = 1\n")
    head = commit(repo, "names")
    diff = run(repo, "diff", f"{base}...{head}", "--", "*.py")
    assert '"b/caf' in diff and "+++ b/my module.py\t" in diff
    assert set(gitread.diff_lines(diff + "\n")) == {"café.py", "my module.py"}


def test_split_diff_keys_each_section_by_its_reviewed_path() -> None:
    diff = (
        section("new.py")
        + "diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
        + "diff --git a/old.py b/moved.py\nrename from old.py\nrename to moved.py\n"
        "--- a/old.py\n+++ b/moved.py\n"
        "@@ -1 +1 @@\n-a\n+b\n"
        + "diff --git a/img.py b/img.py\nBinary files a/img.py and b/img.py differ\n"
    )
    parts = gitread.split_diff(diff)
    assert list(parts) == ["new.py", "gone.py", "moved.py", "img.py"]
    assert "".join(parts.values()) == diff
    assert gitread.renames(diff) == {"old.py": "moved.py"}
    assert gitread.hunk_count(diff) == 3


def test_a_quoted_mode_only_path_is_its_real_file() -> None:
    diff = (
        'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
        "old mode 100644\nnew mode 100755\n" + section("b.py")
    )
    assert list(gitread.split_diff(diff)) == ["café.py", "b.py"]


def test_changed_between_shows_both_sides_of_a_rename(repo: Path) -> None:
    base = run(repo, "rev-parse", "HEAD")
    run(repo, "mv", "keep.py", "kept.py")
    head = commit(repo, "rename")
    assert sorted(gitread.changed_between(base, head, REVIEWED_FILES)) == ["keep.py", "kept.py"]
