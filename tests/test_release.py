"""Tests for ``devloop release-notes`` and ``devloop release-due``: when a release is due.

The end state under test: a commit is released only when it changed the version, the
version is ``X.Y.Z``, no tag has it and the changelog has its section; in every other
case nothing is marked for release.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from review_helpers import run

from devloop import cli, release

CHANGELOG = """\
# Changelog

## [Unreleased]

### Added

## 1.4.0 - 2026-03-02

### Added

- **A new thing.** It does this.
  A second line.

### Fixed

- A fix.

## 1.3.9 - 2026-02-01

### Fixed

- An older fix.
"""
NOTES = "### Added\n\n- **A new thing.** It does this.\n  A second line.\n\n### Fixed\n\n- A fix.\n"


def test_the_notes_are_exactly_the_section_up_to_the_next_heading() -> None:
    assert release.notes(CHANGELOG, "1.4.0") == NOTES
    assert release.notes(CHANGELOG, "1.3.9") == "### Fixed\n\n- An older fix.\n"


@pytest.mark.parametrize(
    ("changelog", "reason"),
    [
        (CHANGELOG.replace("## 1.4.0 - 2026-03-02", "## 1.4.1 - 2026-03-02"), "has no"),
        (CHANGELOG.replace("## 1.4.0 - 2026-03-02", "## 1.4.0"), "has no"),
        (CHANGELOG.replace("## 1.4.0 - 2026-03-02", "## [1.4.0] - 2026-03-02"), "has no"),
        (CHANGELOG.replace("## 1.4.0 - 2026-03-02", "### 1.4.0 - 2026-03-02"), "has no"),
        (CHANGELOG.replace("1.3.9", "1.4.0"), "2 sections"),
        ("## 1.4.0 - 2026-03-02\n\n\n## 1.3.9 - 2026-02-01\n\n- x\n", "is empty"),
        ("", "has no"),
    ],
)
def test_a_changelog_without_one_section_for_the_version_is_refused(
    changelog: str, reason: str
) -> None:
    with pytest.raises(release.ReleaseError, match=reason):
        release.notes(changelog, "1.4.0")


def test_a_version_is_not_matched_inside_another() -> None:
    """``1.4.0`` is not the section of ``11.4.0`` or ``1.4.01``: the dots are literal too."""
    for other in ("11.4.0", "1.4.01", "1x4.0"):
        with pytest.raises(release.ReleaseError, match="has no"):
            release.notes(CHANGELOG.replace("## 1.4.0 -", f"## {other} -"), "1.4.0")


@pytest.mark.parametrize(
    "version",
    ["1.4.0rc1", "1.4.0-rc.1", "1.4.0.dev0", "1.4", "v1.4.0", "1.4.0 ", "01.4.0", "1.4.x", "", "1"],
)
def test_a_pre_release_or_malformed_version_is_refused(version: str) -> None:
    with pytest.raises(release.ReleaseError, match="must be X.Y.Z"):
        release.check_version(version)
    with pytest.raises(release.ReleaseError, match="must be X.Y.Z"):
        release.due(version, "1.3.9", set(), CHANGELOG.replace("1.4.0", version))
    with pytest.raises(release.ReleaseError, match="must be X.Y.Z"):
        release.notes(CHANGELOG, version)


def test_a_version_is_unreleased_until_its_tag_exists() -> None:
    assert release.unreleased("1.4.0", {"v1.3.9", "1.4.0", "v1.4.0-rc1", "v11.4.0"})
    assert not release.unreleased("1.4.0", {"v1.3.9", "v1.4.0"})


def test_a_changed_version_with_no_tag_and_a_section_is_due() -> None:
    expected = release.Release("1.4.0", "v1.4.0", NOTES)
    assert release.due("1.4.0", "1.3.9", {"v1.3.9"}, CHANGELOG) == expected
    assert release.due("1.4.0", None, set(), CHANGELOG) == expected


def test_nothing_is_due_when_the_tag_exists_or_the_version_did_not_change() -> None:
    assert release.due("1.4.0", "1.3.9", {"v1.3.9", "v1.4.0"}, CHANGELOG) is None
    assert release.due("1.4.0", "1.4.0", {"v1.3.9"}, CHANGELOG) is None
    # An unchanged version is not judged at all: no error for one that could not be released.
    assert release.due("1.5.0.dev0", "1.5.0.dev0", set(), CHANGELOG) is None


def test_a_changed_version_without_a_section_is_refused_not_skipped() -> None:
    with pytest.raises(release.ReleaseError, match="has no '## 1.5.0 - <YYYY-MM-DD>' section"):
        release.due("1.5.0", "1.4.0", {"v1.4.0"}, CHANGELOG)


def test_the_project_version_is_read_from_pyproject() -> None:
    assert release.project_version('[project]\nname = "x"\nversion = "1.4.0"\n') == "1.4.0"
    assert release.project_version('[project]\nname = "x"\n') is None
    assert release.project_version("[tool.x]\n") is None
    with pytest.raises(release.ReadError, match="not valid TOML"):
        release.project_version("[project\n")


# The commands, against a real repository with a remote.


def _commit(root: Path, version: str, message: str, changelog: str = CHANGELOG) -> None:
    (root / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{version}"\n')
    (root / "CHANGELOG.md").write_text(changelog)
    (root / "devloop.toml").write_text('[framework]\nversion = "0"\n')
    (root / "note.txt").write_text(message)
    run(root, "add", "-A")
    run(root, "commit", "-q", "-m", message)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Return a clone at 1.3.9 whose remote ``origin`` has the tag ``v1.3.9``."""
    origin, root = tmp_path / "origin.git", tmp_path / "work"
    run(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    root.mkdir()
    run(root, "init", "-q", "-b", "main")
    run(root, "remote", "add", "origin", str(origin))
    _commit(root, "1.3.8", "start")
    _commit(root, "1.3.9", "release 1.3.9")
    run(root, "tag", "-a", "v1.3.9", "-m", "v1.3.9")
    run(root, "push", "-q", "origin", "main", "v1.3.9")
    run(root, "tag", "-d", "v1.3.9")  # the remote is asked, not the clone
    return root


def _due(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str]:
    """Run ``devloop release-due``; return its exit code, its outputs and the notes written."""
    outputs, notes = tmp_path / "outputs", tmp_path / "notes.md"
    outputs.write_text("")
    notes.unlink(missing_ok=True)
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    code = cli.main(["release-due", "--root", str(root), "--output", str(notes)])
    return code, outputs.read_text(), notes.read_text() if notes.exists() else ""


def test_the_commit_that_sets_a_new_version_is_due(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _commit(project, "1.4.0", "release 1.4.0")
    assert _due(project, tmp_path, monkeypatch) == (
        0,
        "release=true\nversion=1.4.0\ntag=v1.4.0\n",
        NOTES,
    )


def test_a_push_that_leaves_the_version_alone_is_not_due(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not the released commit itself, and not the commit after an untagged version."""
    assert _due(project, tmp_path, monkeypatch) == (0, "release=false\n", "")
    _commit(project, "1.4.0", "release 1.4.0")
    _commit(project, "1.4.0", "the next change")
    assert _due(project, tmp_path, monkeypatch) == (0, "release=false\n", "")


def test_an_ordinary_push_reads_no_tags(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A push that leaves the version alone is answered without the remote."""
    _commit(project, "1.3.9", "the next change")
    run(project, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    assert _due(project, tmp_path, monkeypatch) == (0, "release=false\n", "")


def test_a_version_whose_tag_exists_on_the_remote_is_not_due(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _commit(project, "1.4.0", "release 1.4.0")
    run(project, "tag", "-a", "v1.4.0", "-m", "an annotated tag", "HEAD~1")
    run(project, "push", "-q", "origin", "v1.4.0")
    run(project, "tag", "-d", "v1.4.0")
    assert _due(project, tmp_path, monkeypatch) == (0, "release=false\n", "")


def test_a_new_version_with_no_changelog_section_fails_and_marks_nothing(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _commit(project, "1.5.0", "release 1.5.0")
    assert _due(project, tmp_path, monkeypatch) == (1, "", "")
    error = capsys.readouterr().err
    assert "the changelog has no '## 1.5.0 - <YYYY-MM-DD>' section" in error
    assert "nothing was created" in error


def test_a_new_pre_release_version_fails_and_marks_nothing(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _commit(project, "1.4.0rc1", "pre-release", CHANGELOG.replace("1.4.0", "1.4.0rc1"))
    assert _due(project, tmp_path, monkeypatch) == (1, "", "")


def test_tags_that_cannot_be_read_are_an_error_not_an_answer(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _commit(project, "1.4.0", "release 1.4.0")
    run(project, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    assert _due(project, tmp_path, monkeypatch) == (2, "", "")
    assert "could not check: could not read the tags of origin" in capsys.readouterr().err


def test_a_checkout_without_the_parent_commit_is_an_error_not_a_release(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One commit deep, "the version changed" cannot be known, so nothing is due."""
    _commit(project, "1.4.0", "release 1.4.0")
    run(project, "push", "-q", "origin", "main")
    shallow = tmp_path / "shallow"
    origin = (tmp_path / "origin.git").as_uri()
    run(tmp_path, "clone", "-q", "--depth", "1", origin, str(shallow))
    assert _due(shallow, tmp_path, monkeypatch) == (2, "", "")


def test_release_notes_prints_the_section_of_the_project_or_the_named_version(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["release-notes", "--root", str(project)]) == 0
    assert capsys.readouterr().out == "### Fixed\n\n- An older fix.\n"
    assert cli.main(["release-notes", "--root", str(project), "--version", "1.4.0"]) == 0
    assert capsys.readouterr().out == NOTES
    assert cli.main(["release-notes", "--root", str(project), "--version", "9.9.9"]) == 1
    done = capsys.readouterr()
    assert done.out == "" and "has no '## 9.9.9 - <YYYY-MM-DD>' section" in done.err
    assert cli.main(["release-notes", "--root", str(project), "--version", "1.4.0rc1"]) == 1
