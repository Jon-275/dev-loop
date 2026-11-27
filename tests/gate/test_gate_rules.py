"""The merge gate's rules: the lane of a pull request.

Invariants (devloop.gate.rules). Adding a file never makes a strict pull request light. A
path no rule names is strict. Every doubt (an incomplete list, an odd path, a file that
is not a regular file, a rename from elsewhere) is strict.
"""

from __future__ import annotations

import dataclasses
import itertools
import random
import re
import subprocess
from collections.abc import Collection
from pathlib import Path

import pytest

from devloop.config import Config, parse_config
from devloop.gate import rules as gate

ROOT = Path(__file__).resolve().parents[2]
#: The rules of a project that only adopts devloop.
GATE = gate.rules(Config.default("0.1"))


def _file(name: str, status: str = "modified", previous: str | None = None) -> gate.ChangedFile:
    """Return one changed file: a regular file unless a test says otherwise."""
    return gate.ChangedFile(path=name, status=status, previous=previous, mode="100644")


def _of(
    *files: gate.ChangedFile, known: Collection[str] = (), expected: int | None = None
) -> gate.Lane:
    """Return the lane of a pull request that changes these files."""
    count = len(files) if expected is None else expected
    return gate.lane(files, expected=count, known=known, gate=GATE)


def _lane(*names: str) -> gate.Lane:
    """Return the lane of a pull request that modifies these paths."""
    return _of(*(_file(name) for name in names))


LIGHT = (
    "docs/index.md",
    "docs/guides/setup.md",
    "docs/reports/q3/summary.md",
    "docs/img/flow.svg",
    "docs/img/screen.png",
    "README.md",
    "CONTRIBUTING.md",
    "SUPPORT.md",
    "CODE_OF_CONDUCT.md",
    "changes/a-change.fixed.md",
)
CONTROL = (
    ".github/workflows/ci.yml",
    ".github/CODEOWNERS",
    ".github/ISSUE_TEMPLATE/bug.yml",
    "devloop.toml",
    "devloop/lenses/gemini.md",
    "devloop/lessons_seed.json",
    "devloop",
    ".github",
    "pyproject.toml",
    "uv.lock",
    "mkdocs.yml",
    "conftest.py",
    "pkg/tests/conftest.py",
    "Dockerfile",
    "images/Dockerfile.web",
    "docker-compose.yml",
    ".pre-commit-config.yaml",
    ".gitattributes",
    ".gitmodules",
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/skills/pr-workflow/SKILL.md",
    ".agents/skills/council-build/SKILL.md",
    # An agent's instructions and a tool's settings count wherever they sit.
    "docs/AGENTS.md",
    "docs/CLAUDE.md",
    "docs/guides/SKILL.md",
    "docs/.claude/commands/x.md",
    "docs/.github/workflows/x.yml",
    "docs/AGENTS.override.md",
    "docs/claude.local.md",
    "docs/GEMINI.md",
    "docs/CODEOWNERS",
    "docs/evil.pth",
    "pkg/pyproject.toml",
    "pkg/setup.py",
    "pkg/sitecustomize.py",
    "pkg/usercustomize.py",
    "mypy.ini",
    "tests/ruff.toml",
    "pytest.ini",
    "pytest.toml",
    "setup.cfg",
    "tox.ini",
    "uv.toml",
    # Any hidden file or folder.
    ".devloop/manifest.json",
    ".mcp.json",
    ".vscode/settings.json",
    "docs/.hidden/a.md",
    "docs/.a.md",
    "tests/.mypy.ini",
    # What devloop is configured to read: the lessons page.
    "docs/lessons-learned.md",
)
CORE = (
    "src/app/billing.py",
    "pkg/README.md",
    "tests/test_app.py",
    "scripts/run.py",
    "LICENSE",
    "CHANGELOG.md",
    "docs/guides/notebook.ipynb",
    "docs/model.pkl",
    "docs/README.MD",
    "Docs/README.md",
    "changes/nested/a.md",
    "notes.md",
    "NOTES.md",
    "some/new/thing.txt",
    "docs/page.py",
    "docs/page.html",
    "docs/page.js",
    "docs/page.css",
    "docs/page.txt",
    "docs/page.pdf",
    "docsx/a.md",
    "docs.md",
    "changes/a.txt",
    "changes/a.md.py",
    "docs/$(x).md",
    "docs/-rf.md",
    "docs/a b.md",
)


@pytest.mark.parametrize("name", LIGHT)
def test_a_document_on_the_allow_list_is_light(name: str) -> None:
    """Documents and their images, the front pages and changelog fragments."""
    assert _lane(name) == gate.Lane(light=True, control=False, reason="documents only")


@pytest.mark.parametrize("name", CONTROL)
def test_a_control_file_is_strict_and_flagged(name: str) -> None:
    """What defines, configures or is executed by a check, a reviewer or an agent."""
    found = _lane(name)
    assert (found.light, found.control) == (False, True), name
    # ... also beside documents, and whatever its case.
    assert _lane("docs/index.md", name).control is True
    assert _lane(name.upper()).light is False


@pytest.mark.parametrize("name", CORE)
def test_everything_else_is_strict(name: str) -> None:
    """Core code, a path nobody thought of, and a document outside the allow-list."""
    found = _lane(name)
    assert (found.light, found.control) == (False, False), name


def test_a_project_adds_control_files_and_cannot_remove_a_built_in_one() -> None:
    """``gate.control`` adds; a wide ``gate.light`` never makes a control file a document."""
    text = '[framework]\nversion = "0.1"\n[skills]\nproject = "team/skills"\n'
    text += '[gate]\nlight = ["**"]\ncontrol = ["scripts/**", "docs/decisions/*.md", "LICENSE"]\n'
    own = gate.rules(parse_config(text))
    for name in ("scripts/run.py", "docs/decisions/0001.md", "license", "team/skills/x/notes.md"):
        found = gate.lane([_file(name)], expected=1, known=(), gate=own)
        assert (found.light, found.control) == (False, True), name
    for name in CONTROL:
        assert gate.lane([_file(name)], expected=1, known=(), gate=own).control is True, name
    # A folder deeper than the pattern names is not matched by a single star.
    deeper = gate.lane([_file("docs/decisions/old/0001.md")], expected=1, known=(), gate=own)
    assert (deeper.light, deeper.control) == (True, False)


@pytest.mark.parametrize(
    ("glob", "path", "expected"),
    [
        ("changes/*.md", "changes/a.md", True),
        ("changes/*.md", "changes/x/a.md", False),
        ("docs/**/*.md", "docs/a.md", True),
        ("docs/**/*.md", "docs/x/y/a.md", True),
        ("docs/**/*.md", "docsx/a.md", False),
        ("docs/**", "docs/x/y.png", True),
        ("docs/**", "docs", False),
        ("**/*.lock", "a.lock", True),
        ("**/*.lock", "x/y/a.lock", True),
        ("a?.md", "ab.md", True),
        ("a?.md", "a/.md", False),
        ("a.md", "aXmd", False),
        ("a.md", "x/a.md", False),
    ],
)
def test_a_star_stays_inside_one_folder_and_two_cross_folders(
    glob: str, path: str, expected: bool
) -> None:
    """The whole path must match; a dot is a dot."""
    assert gate.matches(path, [glob]) is expected


def test_a_mixed_pull_request_is_strict() -> None:
    """One file outside the allow-list makes the whole pull request strict."""
    assert _lane("docs/index.md", "src/app/x.py").light is False


def test_an_empty_or_incomplete_file_list_is_strict() -> None:
    """Nothing to judge, more than the gate will read, or a count that does not add up."""
    # What cannot be seen may be a control file, so it is flagged as one.
    assert _of() == gate.Lane(False, True, "no changed file could be read")
    for expected in (0, 2):
        short = _of(_file("docs/a.md"), expected=expected)
        assert (short.light, short.control) == (False, True), expected
    twice = _of(_file("docs/a.md"), _file("docs/a.md"))
    assert (twice.light, twice.control) == (False, True)
    many = [_file(f"docs/page-{i}.md") for i in range(GATE.max_light_files + 1)]
    assert _of(*many).light is False
    assert _of(*many[:-1]).light is True
    fewer = dataclasses.replace(GATE, max_light_files=2)
    assert gate.lane(many[:3], expected=3, known=(), gate=fewer).light is False
    assert gate.lane(many[:2], expected=2, known=(), gate=fewer).light is True


def test_a_rename_is_judged_by_both_names() -> None:
    """A core file moved under docs/ is a change to core."""
    assert _of(_file("docs/guide.md", "renamed", "src/app/x.py")).light is False
    assert _of(_file("docs/guide.md", "renamed", "devloop.toml")).control is True
    assert _of(_file("docs/new.md", "renamed", "docs/old.md")).light is True
    assert _of(_file("docs/new.md", "renamed", None)).light is False
    # An earlier name on a change that is not a rename is judged too, and is odd.
    assert _of(_file("docs/new.md", "modified", "src/app/x.py")).light is False
    assert _of(_file("docs/new.md", "modified", "docs/old.md")).light is False


@pytest.mark.parametrize("mode", ["120000", "160000", "100755", "040000", "", None])
def test_only_a_regular_file_is_light(mode: str | None) -> None:
    """A symbolic link, a submodule, an executable or an unknown kind is strict."""
    for status, previous in (("added", None), ("modified", None), ("renamed", "docs/b.md")):
        odd = gate.ChangedFile(path="docs/a.md", status=status, previous=previous, mode=mode)
        assert _of(odd).light is False, status


def test_a_removed_document_is_light_without_a_mode() -> None:
    """A deleted file has no entry in the new tree."""
    assert _of(gate.ChangedFile("docs/a.md", "removed", None, None)).light is True


@pytest.mark.parametrize("status", ["copied", "changed", "unchanged", "", "weird"])
def test_an_unknown_kind_of_change_is_strict(status: str) -> None:
    """Only added, modified, removed and renamed are understood."""
    assert _of(_file("docs/a.md", status)).light is False


@pytest.mark.parametrize(
    "name",
    [
        "docs/a" + chr(0x202E) + "b.md",
        "docs/a\nb.md",
        "docs/a\\b.md",
        "docs/../src/x.md",
        "docs//a.md",
        "/docs/a.md",
        "docs/" + "a" * 300 + ".md",
        "docs/" + "a" * 193 + ".md",
        "docs/a.md ",
        "docs/a.md\n",
        "docs/./a.md",
        "docs/caf" + chr(0xE9) + ".md",
    ],
)
def test_an_odd_path_is_strict(name: str) -> None:
    """Hidden characters, a backslash, a climb out of the folder, or a very long name."""
    assert _lane(name).light is False


@pytest.mark.parametrize(
    "name", ["docs/.a.md", ".docs/a.md", "docs/x/.hidden/a.md", "docs/-a.md", "-docs/a.md"]
)
def test_no_part_of_a_plain_path_starts_with_a_dot_or_a_dash(name: str) -> None:
    """The path check refuses a hidden part itself, not only because it is a control file."""
    assert gate._is_plain(name) is False
    assert gate._is_plain(name.replace("/.", "/").replace("/-", "/").lstrip(".-")) is True


def test_a_path_exactly_at_the_length_limit_is_still_a_document() -> None:
    """The limit is the limit, not one less."""
    name = "docs/" + "a" * (gate.MAX_PATH - 8) + ".md"
    assert len(name) == gate.MAX_PATH and _lane(name).light is True


def test_a_new_file_that_differs_from_an_existing_one_only_by_case_is_strict() -> None:
    """On a system that ignores case it would replace the existing file."""
    known = ["docs/README.md", "docs/guides/setup.md"]
    assert _of(_file("docs/readme.md", "added"), known=known).light is False
    # ... whatever the kind of change, and wherever in the list it is.
    for status in ("modified", "removed"):
        assert _of(_file("docs/readme.md", status), known=known).light is False
    later = [_file("docs/new.md", "added"), _file("docs/guides/SETUP.md", "added")]
    assert _of(*later, known=known).light is False
    # Two new files that differ only by case cannot both exist everywhere.
    pair = [_file("docs/New.md", "added"), _file("docs/new.md", "added")]
    assert _of(*pair, known=known).light is False
    assert _of(_file("docs/README.md"), known=known).light is True
    assert _of(_file("docs/new.md", "added"), known=known).light is True


def test_the_paths_of_the_default_branch_and_the_count_must_be_given_properly() -> None:
    """A caller cannot pass one path for the list, or a flag as a count."""
    with pytest.raises(TypeError):
        _of(_file("docs/a.md"), known="docs/a.md")
    with pytest.raises(TypeError):
        _of(_file("docs/a.md"), expected=True)


def test_a_control_file_is_flagged_even_among_more_files_than_a_light_change_may_have() -> None:
    """The file limit never hides a control file."""
    many = [_file(f"docs/page-{i}.md") for i in range(GATE.max_light_files + 5)]
    assert _of(*many, _file("devloop.toml")).control is True


def _tracked() -> list[str]:
    """Return every file git tracks in this checkout."""
    listed = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return listed.stdout.splitlines()


def test_the_light_files_of_this_repository_are_what_a_reader_would_call_documents() -> None:
    """An independent rule, written another way, agrees on every tracked file."""
    pattern = re.compile(
        r"^(?:README|CONTRIBUTING|SUPPORT|CODE_OF_CONDUCT)\.md$"
        r"|^changes/[^/]+\.md$"
        r"|^docs/(?:[^/]+/)*[^/]+\.(?:md|svg|png|jpg|jpeg|gif|webp)$"
    )
    tracked = _tracked()
    light = {name for name in tracked if _lane(name).light}
    assert len(light) >= 15
    assert all(pattern.match(name) for name in light)
    # Nothing a tool runs or loads is a document.
    risky = (".py", ".ipynb", ".yml", ".yaml", ".toml", ".json", ".sh", ".html", ".js", ".css")
    assert not [name for name in light if name.endswith(risky)]
    # What the oracle calls a document and the gate does not is a control file.
    for name in tracked:
        if pattern.match(name) and name not in light:
            assert _lane(name).control is True, name


def test_every_workflow_agent_file_and_setting_tracked_here_is_a_control_file() -> None:
    """The real files a check, a reviewer or an agent reads, not only examples of them."""
    must = [
        name
        for name in _tracked()
        if name.startswith((".github/", ".claude/", ".agents/", "devloop/"))
        or name.rsplit("/", 1)[-1] in ("pyproject.toml", "conftest.py", "AGENTS.md", "CLAUDE.md")
    ]
    assert len(must) > 20
    assert [name for name in must if not _lane(name).control] == []


PATHS = LIGHT + CONTROL + CORE
DOCUMENTS = (".md", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp")


def _stricter(before: gate.Lane, after: gate.Lane) -> bool:
    """Return whether ``after`` is no lighter and no less flagged than ``before``."""
    return not (after.light and not before.light) and not (before.control and not after.control)


def test_adding_a_file_never_makes_a_strict_pull_request_light() -> None:
    """The lane only ever gets stricter as files are added: every pair, and many longer lists."""
    for name, extra in itertools.product(PATHS, repeat=2):
        assert _stricter(_lane(name), _lane(name, extra)), (name, extra)
    pick = random.Random(20250101)
    for _ in range(2000):
        names = pick.sample(PATHS, pick.randint(1, 6))
        extra = pick.choice(PATHS)
        assert _stricter(_lane(*names), _lane(*names, extra)), (names, extra)


def test_a_path_made_of_anything_is_never_light_unless_the_allow_list_names_it() -> None:
    """No pattern is loose enough for an arbitrary string to pass as a document."""
    pick = random.Random(20250102)
    letters = "abdcmosvg/.-_ *?$\\\n\t" + chr(0xE9) + chr(0x202E) + "DOCSREADME"
    made = ["".join(pick.choices(letters, k=pick.randint(1, 40))) for _ in range(3000)]
    # ... and real paths with one character changed, dropped or added.
    for name in PATHS:
        at = pick.randrange(len(name))
        made += [name[:at] + pick.choice(letters) + name[at + 1 :], name[:at] + name[at + 1 :]]
        made.append(name[:at] + pick.choice(letters) + name[at:])
    for name in made:
        if _lane(name).light:
            assert name in ("README.md", "CONTRIBUTING.md", "SUPPORT.md", "CODE_OF_CONDUCT.md") or (
                name.startswith(("docs/", "changes/"))
                and name.isascii()
                and name.endswith(DOCUMENTS)
            ), name
