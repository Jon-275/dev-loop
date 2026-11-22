"""Tests for ``devloop changelog``: fragments in ``changes/`` built into ``CHANGELOG.md``."""

import subprocess
from pathlib import Path

import pytest
from conftest import MergeRepo

from devloop import changelog
from devloop.cli import main
from devloop.config import Changelog

LOG = Changelog()
CHANGELOG = """# Changelog

## [Unreleased]

### Breaking changes

Read these first.

- old breaking

### Added

- old added

### Fixed

- old fixed

### Security

### Changed

- old changed

## Earlier development

### Added

- ancient
"""


def _repo(tmp_path: Path, fragments: dict[str, str]) -> Path:
    """Write a project with a changelog and ``fragments`` (name: text)."""
    (tmp_path / "devloop.toml").write_text('[framework]\nversion = "0.1"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes" / "README.md").write_text("# not a fragment\n", encoding="utf-8")
    for name, text in fragments.items():
        (tmp_path / "changes" / name).write_text(text, encoding="utf-8")
    return tmp_path


def _log(root: Path) -> str:
    return (root / "CHANGELOG.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["a.added.md", "fix-2.fixed.md", "x.breaking.md"])
def test_check_accepts_good_names(tmp_path: Path, name: str) -> None:
    root = _repo(tmp_path, {name: "- entry\n"})
    assert changelog.check(root, LOG) == []
    assert main(["changelog", "--root", str(root), "check"]) == 0


@pytest.mark.parametrize(
    ("name", "text", "error"),
    [
        ("a.md", "- x\n", "name must be"),
        ("Bad.added.md", "- x\n", "name must be"),
        ("a.added.txt", "- x\n", "name must be"),
        ("a.removed.md", "- x\n", "unknown section 'removed'"),
        ("a.added.md", " \n\n", "empty fragment"),
    ],
)
def test_check_rejects_bad_names_sections_and_empty_files(
    tmp_path: Path, name: str, text: str, error: str
) -> None:
    root = _repo(tmp_path, {name: text})
    errors = changelog.check(root, LOG)
    assert len(errors) == 1 and error in errors[0]
    assert main(["changelog", "--root", str(root), "check"]) == 1
    with pytest.raises(ValueError, match=error):
        changelog.build(root, LOG)
    assert _log(root) == CHANGELOG


def test_sections_come_from_the_config(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"a.feature.md": "- x\n"})
    assert changelog.check(root, Changelog(sections={"feature": "Features"})) == []


def test_build_puts_each_fragment_at_the_top_of_its_section_in_name_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = Path.iterdir  # listed in reverse, so only sorting gives name order
    monkeypatch.setattr(Path, "iterdir", lambda self: iter(sorted(listing(self), reverse=True)))
    root = _repo(
        tmp_path,
        {
            "b-second.added.md": "- second added\n",
            "a-first.added.md": "first added\nwraps here\n",
            "brk.breaking.md": "- new breaking\n",
            "sec.security.md": "- new security\n",
            "fix.fixed.md": "- new fixed\n  continued\n",
        },
    )
    result = changelog.build(root, LOG)
    assert result == _log(root)
    unreleased, earlier = result.split("## Earlier development")
    assert "Read these first.\n\n- new breaking\n- old breaking\n" in unreleased
    assert "### Added\n\n- first added\n  wraps here\n- second added\n- old added\n" in unreleased
    assert "### Fixed\n\n- new fixed\n  continued\n- old fixed\n" in unreleased
    assert "### Security\n\n- new security\n\n### Changed\n\n- old changed\n" in unreleased
    assert earlier == CHANGELOG.split("## Earlier development")[1]


def test_build_is_idempotent_and_deletes_the_fragments(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"a.added.md": "- new added\n", "b.changed.md": "- new changed\n"})
    assert main(["changelog", "--root", str(root), "build"]) == 0
    once = _log(root)
    assert sorted(p.name for p in (root / "changes").iterdir()) == ["README.md"]
    assert main(["changelog", "--root", str(root), "build"]) == 0
    assert _log(root) == once
    (root / "changes" / "a.added.md").write_text("- new added\n", encoding="utf-8")
    assert changelog.build(root, LOG) == once  # a leftover is not inserted twice
    assert not (root / "changes" / "a.added.md").exists()


def test_dry_run_prints_the_result_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(tmp_path, {"a.added.md": "- new added\n"})
    assert main(["changelog", "--root", str(root), "build", "--dry-run"]) == 0
    assert "### Added\n\n- new added\n- old added\n" in capsys.readouterr().out
    assert _log(root) == CHANGELOG
    assert (root / "changes" / "a.added.md").exists()


def test_a_missing_section_heading_is_an_error(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"a.added.md": "- x\n"})
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no '### Added' heading"):
        changelog.build(root, LOG)


@pytest.mark.parametrize(
    ("changed", "note"),
    [
        ([("M", "src/x/a.py")], True),
        ([("A", ".github/workflows/a.yml"), ("M", "tests/test_a.py")], True),
        ([("M", "src/x/a.py"), ("A", "changes/a.added.md")], False),
        ([("M", "src/x/a.py"), ("M", "CHANGELOG.md")], False),
        ([("M", "docs/a.md"), ("M", "tests/test_a.py")], False),
        ([("M", "src/x/a.py"), ("M", "changes/README.md")], True),
        ([("M", "src/x/a.py"), ("D", "changes/a.added.md")], True),
        ([("M", "src/x/a.py"), ("A", "changes/a.removed.md")], True),
        ([("M", "src/x/a.py"), ("A", "changes/sub/a.added.md")], True),
    ],
)
def test_a_code_change_with_no_entry_is_noted(changed: list[tuple[str, str]], note: bool) -> None:
    assert changelog.needs_entry(changed, LOG) is note


def test_the_paths_that_need_an_entry_come_from_the_config() -> None:
    assert changelog.needs_entry([("M", "packages/a.py")], Changelog(code=("packages/*",)))
    assert not changelog.needs_entry([("M", "src/a.py")], Changelog(code=("packages/*",)))


def test_a_fragment_that_is_a_prefix_of_an_entry_is_still_inserted(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"a.fixed.md": "- old\n"})
    (root / "CHANGELOG.md").write_text(
        CHANGELOG.replace("- old fixed", "- old fixed for JSON"), encoding="utf-8"
    )
    assert "### Fixed\n\n- old\n- old fixed for JSON\n" in changelog.build(root, LOG)


def test_a_two_paragraph_fragment_is_inserted_once(tmp_path: Path) -> None:
    text = "- Summary\n\n  More detail\n"
    root = _repo(tmp_path, {"a.added.md": text})
    once = changelog.build(root, LOG)
    assert "### Added\n\n- Summary\n\n  More detail\n- old added\n" in once
    (root / "changes" / "a.added.md").write_text(text, encoding="utf-8")
    assert changelog.build(root, LOG) == once


def test_identical_fragments_in_one_build_insert_one_entry(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"a.added.md": "- same\n", "b.added.md": "- same\n"})
    assert changelog.build(root, LOG).count("- same\n") == 1


def test_pr_mode_notes_a_code_change_without_an_entry(
    merge_repo: MergeRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = merge_repo({"src/a.py": "x\n"}, base={"devloop.toml": '[framework]\nversion = "0.1"\n'})
    assert main(["changelog", "--root", str(repo), "check", "--pr"]) == 0
    assert "adds no changes/<slug>.<section>.md fragment" in capsys.readouterr().out


def test_an_unreadable_change_skips_the_note_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(tmp_path, {})

    def broken(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(subprocess, "run", broken)
    assert main(["changelog", "--root", str(root), "check", "--pr"]) == 0
    assert "cannot read the change" in capsys.readouterr().out
