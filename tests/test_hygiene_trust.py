"""A pull request does not choose the rules that judge it.

With ``--config-from``, ``devloop changes``, ``devloop size`` and ``devloop changelog
check`` read ``devloop.toml`` from the base commit, not from the pull request.
"""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import MergeRepo, git, lines

from devloop.changes import docs_only
from devloop.cli import main
from devloop.config import ConfigError, Docs
from devloop.rules import base_config

TOML = '[framework]\nversion = "0.1"\n'
BASE = "HEAD^1"
WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _changes(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> str:
    """Run ``devloop changes`` on a pull request and return what it wrote for Actions."""
    out = tmp_path / "out.txt"
    out.unlink(missing_ok=True)  # the command appends
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert main(["changes", "--root", str(repo), "--event", "pull_request", *extra]) == 0
    return out.read_text(encoding="utf-8")


def _size(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> int:
    """Run ``devloop size`` on an unlabelled pull request, with no API to read labels from."""
    payload = tmp_path / "event.json"
    payload.write_text(json.dumps({"pull_request": {"number": 7, "labels": []}}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(payload))
    for name in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(name, raising=False)
    return main(["size", "--root", str(repo), "--event", "pull_request", *extra])


def test_a_change_to_the_configuration_is_always_code() -> None:
    wide = Docs(files=("*.md", "*.toml", "devloop.toml"))
    assert not docs_only(["devloop.toml"], wide)
    assert not docs_only(["README.md", "devloop.toml"], wide)
    assert docs_only(["pyproject.toml"], wide)  # only the configuration file is special


def test_a_pr_that_changes_only_the_configuration_is_code(
    merge_repo: MergeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = TOML + '[docs]\nfiles = ["*.md", "*.toml"]\n'
    repo = merge_repo({"devloop.toml": base + "# a note\n"}, base={"devloop.toml": base})
    for extra in ((), ("--config-from", BASE)):
        assert _changes(repo, tmp_path, monkeypatch, *extra).startswith("code=true\n")


def test_a_pr_cannot_widen_the_documents_to_cover_its_code(
    merge_repo: MergeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    widened = TOML + '[docs]\nfiles = ["*.md", "*.py", "devloop.toml"]\n'
    repo = merge_repo({"a.py": "x = 1\n", "devloop.toml": widened}, base={"devloop.toml": TOML})
    assert _changes(repo, tmp_path, monkeypatch, "--config-from", BASE).startswith("code=true\n")


def test_a_docs_only_pr_is_still_docs_only_by_the_base_rules(
    merge_repo: MergeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = merge_repo({"README.md": "better\n"}, base={"devloop.toml": TOML})
    assert _changes(repo, tmp_path, monkeypatch, "--config-from", BASE).startswith("code=false\n")


def test_a_pr_cannot_raise_its_own_size_limit(
    merge_repo: MergeRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = merge_repo(
        {"a.py": lines(50), "devloop.toml": TOML + "[pr]\nmax_lines = 10000\n"},
        base={"devloop.toml": TOML + "[pr]\nmax_lines = 10\n"},
    )
    assert _size(repo, tmp_path, monkeypatch, "--config-from", BASE) == 1
    assert "over the 10-line limit" in capsys.readouterr().out
    assert _size(repo, tmp_path, monkeypatch) == 0  # without the option: the file on disk


def test_a_pr_cannot_exclude_its_own_lines_from_the_count(
    merge_repo: MergeRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    limit = TOML + "[pr]\nmax_lines = 10\n"
    repo = merge_repo(
        {"big.py": lines(50), "devloop.toml": limit + 'exclude = ["big.py"]\n'},
        base={"devloop.toml": limit},
    )
    assert _size(repo, tmp_path, monkeypatch, "--config-from", BASE) == 1
    assert "Counted: 51 changed lines" in capsys.readouterr().out
    assert _size(repo, tmp_path, monkeypatch) == 0
    assert "matches pr.exclude 'big.py'" in capsys.readouterr().out


def test_a_pr_cannot_move_the_changelog_folder_away_from_a_bad_fragment(
    merge_repo: MergeRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    moved = TOML + '[changelog]\ndir = "elsewhere"\n'
    repo = merge_repo(
        {"changes/Bad Name.md": "x\n", "devloop.toml": moved}, base={"devloop.toml": TOML}
    )
    assert main(["changelog", "--root", str(repo), "check", "--config-from", BASE]) == 1
    assert "Bad Name.md" in capsys.readouterr().err
    assert main(["changelog", "--root", str(repo), "check"]) == 0


def test_a_base_with_no_configuration_falls_back_to_the_prs_and_says_so(
    merge_repo: MergeRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    adopted = TOML + "[pr]\nmax_lines = 10\n"
    repo = merge_repo({"a.py": lines(50), "devloop.toml": adopted})
    config, note = base_config(repo, BASE)
    assert config.pr.max_lines == 10
    assert "has no devloop.toml" in note and "pull request's own" in note
    assert _size(repo, tmp_path, monkeypatch, "--config-from", BASE) == 1
    assert note in capsys.readouterr().out
    assert _changes(repo, tmp_path, monkeypatch, "--config-from", BASE).startswith("code=true\n")
    assert note in capsys.readouterr().out
    assert main(["changelog", "--root", str(repo), "check", "--config-from", BASE]) == 0
    assert note in capsys.readouterr().out


def test_a_readable_base_configuration_has_no_note(merge_repo: MergeRepo) -> None:
    repo = merge_repo({"a.py": "x\n"}, base={"devloop.toml": TOML + "[pr]\nmax_lines = 7\n"})
    config, note = base_config(repo, BASE)
    assert (config.pr.max_lines, note) == (7, "")


def _invalid(build: MergeRepo) -> tuple[Path, str]:
    """Return a repository whose base configuration is not valid, and the base revision."""
    return build({"devloop.toml": TOML}, base={"devloop.toml": "[pr\n"}), BASE


def _unknown_revision(build: MergeRepo) -> tuple[Path, str]:
    """Return a repository and a base revision it does not hold (a shallow checkout)."""
    return build({"a.py": "x\n"}, base={"devloop.toml": TOML}), "0" * 40


def _option(build: MergeRepo) -> tuple[Path, str]:
    """Return a repository and a "revision" that is really a git option."""
    return build({"a.py": "x\n"}, base={"devloop.toml": TOML}), "--output=x"


def _symlink(build: MergeRepo) -> tuple[Path, str]:
    """Return a repository whose base configuration is a symlink to another file."""
    repo = build({"a.py": "x\n"}, base={"real.toml": TOML})
    git(repo, "checkout", "-q", "--detach", "HEAD^1")
    (repo / "devloop.toml").symlink_to("real.toml")
    git(repo, "add", "devloop.toml")
    git(repo, "commit", "-q", "-m", "link")
    return repo, "HEAD"


def _not_utf8(build: MergeRepo) -> tuple[Path, str]:
    """Return a repository whose base configuration is not UTF-8 text."""
    repo = build({"a.py": "x\n"}, base={"other.txt": "x\n"})
    git(repo, "checkout", "-q", "--detach", "HEAD^1")
    (repo / "devloop.toml").write_bytes(b'[framework]\nversion = "\xff\xfe"\n')
    git(repo, "add", "devloop.toml")
    git(repo, "commit", "-q", "-m", "bytes")
    return repo, "HEAD"


def _not_a_repository(build: MergeRepo) -> tuple[Path, str]:
    """Return a folder that holds a valid configuration but is not a git repository."""
    repo = build({"devloop.toml": TOML})
    (repo / ".git").rename(repo / "gone")
    return repo, BASE


CASES = [_invalid, _unknown_revision, _option, _symlink, _not_utf8, _not_a_repository]


@pytest.mark.parametrize("case", CASES, ids=[case.__name__.strip("_") for case in CASES])
def test_a_base_configuration_that_cannot_be_used_fails_closed(
    case: Callable[[MergeRepo], tuple[Path, str]],
    merge_repo: MergeRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, rev = case(merge_repo)
    with pytest.raises(ConfigError, match="base"):
        base_config(repo, rev)
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    root = ["--root", str(repo)]
    option = f"--config-from={rev}"
    assert main(["changes", *root, "--event", "pull_request", option]) == 2
    assert out.read_text(encoding="utf-8") == "code=true\ndoc_tests=\n"  # never docs-only
    assert main(["size", *root, "--event", "pull_request", option]) == 2
    assert main(["changelog", *root, "check", option]) == 2
    assert capsys.readouterr().err.count("base") >= 3


def test_other_events_do_not_read_the_base(
    merge_repo: MergeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = merge_repo({"devloop.toml": TOML}, base={"devloop.toml": "[pr\n"})
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert main(["changes", "--root", str(repo), "--event", "push", "--config-from", BASE]) == 0
    assert out.read_text(encoding="utf-8").startswith("code=true\n")


def test_without_the_option_the_file_on_disk_decides(
    merge_repo: MergeRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    widened = TOML + '[docs]\nfiles = ["*.md", "*.txt"]\n'
    repo = merge_repo({"notes.txt": "x\n"}, base={"devloop.toml": widened})
    (repo / "devloop.toml").write_text(TOML, encoding="utf-8")  # uncommitted, stricter
    assert _changes(repo, tmp_path, monkeypatch).startswith("code=true\n")
    (repo / "devloop.toml").write_text(widened, encoding="utf-8")
    assert _changes(repo, tmp_path, monkeypatch).startswith("code=false\n")


# The workflows


def _load(name: str) -> dict[Any, Any]:
    """Return one workflow file, parsed."""
    data: dict[Any, Any] = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return data


def _runs(job: dict[str, Any]) -> str:
    """Return every shell command of ``job``, joined."""
    return "\n".join(step.get("run", "") for step in job["steps"])


@pytest.mark.parametrize("name", ["changes", "size", "changelog"])
def test_each_pr_check_takes_its_rules_from_the_base(name: str) -> None:
    job = _load("pr-hygiene.yml")["jobs"][name]
    command = next(line for line in _runs(job).splitlines() if line.startswith("devloop "))
    assert "--config-from HEAD^1" in command, command
    assert job["steps"][0]["with"]["fetch-depth"] == 2  # so HEAD^1, the base, is there


def test_doctor_checks_the_prs_own_configuration() -> None:
    job = _load("pr-hygiene.yml")["jobs"]["doctor"]
    assert "devloop doctor --check" in _runs(job)
    assert "--config-from" not in _runs(job)


def test_every_shared_workflow_installs_the_same_uv_and_python() -> None:
    pins: dict[str, set[tuple[str, str]]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        data = _load(path.name)
        trigger = data["on"] if "on" in data else data[True]  # YAML 1.1: on is True
        if "workflow_call" not in trigger:
            continue
        for job in data["jobs"].values():
            for step in job.get("steps", []):
                if step.get("uses", "").startswith("astral-sh/setup-uv@"):
                    settings = step.get("with", {})
                    pin = (str(settings.get("version")), str(settings.get("python-version")))
                    pins.setdefault(path.name, set()).add(pin)
    assert "pr-hygiene.yml" in pins and "labels.yml" in pins
    assert len({pin for found in pins.values() for pin in found}) == 1, pins
    version, python = next(iter(pins["labels.yml"]))
    assert re.fullmatch(r"\d+\.\d+\.\d+", version) and re.fullmatch(r"3\.\d+\.\d+", python)
