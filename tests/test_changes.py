"""Tests for ``devloop changes``, which spots a pull request that changes only documentation."""

from pathlib import Path

import pytest
from conftest import MergeRepo, git

from devloop.changes import decide, doc_tests, docs_only
from devloop.cli import main
from devloop.config import Docs

DOCS = Docs()


@pytest.mark.parametrize(
    "paths",
    [["README.md"], ["docs/guide.md", "src/pkg/README.md"], ["docs/img/a.PNG", "x.md"]],
)
def test_documents_alone_are_docs_only(paths: list[str]) -> None:
    assert docs_only(paths, DOCS) is True


@pytest.mark.parametrize(
    "paths",
    [
        [],
        ["README.md", "src/a.py"],
        ["devloop.toml"],
        ["img/a.png"],
        [".github/pull_request_template.md"],
        [".github/workflows/ci.yml"],
    ],
)
def test_anything_else_or_nothing_counts_as_code(paths: list[str]) -> None:
    assert docs_only(paths, DOCS) is False


def test_what_counts_as_documentation_comes_from_the_config() -> None:
    assert docs_only(["guide.rst"], Docs(files=("*.rst",)))
    assert not docs_only(["README.md"], Docs(files=("*.rst",)))


def test_doc_tests_are_the_test_files_that_name_a_markdown_file(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_readme.py").write_text('README = "README.md"\n', encoding="utf-8")
    (tmp_path / "tests" / "test_code.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "tests").mkdir(parents=True)
    (tmp_path / "pkg" / "tests" / "test_doc.py").write_text("p = 'a.md'\n", encoding="utf-8")
    (tmp_path / "tests" / "test_untracked.py").write_text("p = 'a.md'\n", encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "tests/test_readme.py", "tests/test_code.py", "pkg")
    assert doc_tests(tmp_path, DOCS) == ["tests/test_readme.py"]
    wider = Docs(tests=("tests/*test_*.py", "pkg/tests/*test_*.py"))
    assert doc_tests(tmp_path, wider) == ["pkg/tests/test_doc.py", "tests/test_readme.py"]


def test_a_docs_only_pr_is_read_from_the_merge_commit(merge_repo: MergeRepo) -> None:
    repo = merge_repo({"README.md": "better\n", "docs/guide.md": "new\n"})
    assert decide("pull_request", repo, DOCS) == (False, ["README.md", "docs/guide.md"])


def test_a_pr_that_changes_code_runs_everything(merge_repo: MergeRepo) -> None:
    assert decide("pull_request", merge_repo({"README.md": "x\n", "a.py": "y\n"}), DOCS)[0]


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", "schedule"])
def test_every_other_event_runs_everything(merge_repo: MergeRepo, event: str) -> None:
    assert decide(event, merge_repo({"README.md": "better\n"}), DOCS) == (True, [])


def test_a_head_that_is_not_a_merge_runs_everything(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    assert decide("pull_request", tmp_path, DOCS) == (True, [])


def test_a_git_error_runs_everything(tmp_path: Path) -> None:
    assert decide("pull_request", tmp_path / "not-a-repo", DOCS) == (True, [])


def test_the_command_writes_code_and_doc_tests_to_the_output(
    merge_repo: MergeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = merge_repo(
        {"README.md": "better\n"},
        base={
            "devloop.toml": '[framework]\nversion = "0.1"\n',
            "tests/test_readme.py": "p = 'README.md'\n",
        },
    )
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert main(["changes", "--root", str(repo), "--event", "pull_request"]) == 0
    assert out.read_text(encoding="utf-8") == "code=false\ndoc_tests=tests/test_readme.py\n"


def test_skill_folders_are_code_not_documentation(merge_repo: MergeRepo) -> None:
    skills = ("devloop/skills", ".claude/skills")
    assert not docs_only(["devloop/skills/s/SKILL.md"], DOCS, skills)
    assert not docs_only([".claude/skills/s/SKILL.md", "README.md"], DOCS, skills)
    assert docs_only(["docs/skills.md"], DOCS, skills)
    repo = merge_repo({"devloop/skills/s/SKILL.md": "x\n"})
    assert decide("pull_request", repo, DOCS, skills)[0]
