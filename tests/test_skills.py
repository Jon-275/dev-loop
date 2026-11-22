"""Tests for loading skills and enforcing the portable SKILL.md frontmatter."""

from pathlib import Path

import pytest

from devloop.skills import SkillError, framework_skills, load_skill, parse_frontmatter


def _skill(root: Path, folder: str, head: str, body: str = "Do the thing.\n") -> Path:
    path = root / folder
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\n{head}---\n{body}", encoding="utf-8")
    return path


def test_load_skill_reads_every_file_in_the_folder(tmp_path: Path) -> None:
    path = _skill(tmp_path, "pr-flow", "name: pr-flow\ndescription: Open a PR.\n")
    (path / "references").mkdir()
    (path / "references" / "x.md").write_text("ref\n", encoding="utf-8")
    skill = load_skill(path)
    assert skill.name == "pr-flow"
    assert skill.description == "Open a PR."
    assert set(skill.files) == {"SKILL.md", "references/x.md"}


def test_quoted_values_are_unquoted() -> None:
    meta = parse_frontmatter('---\nname: a\ndescription: "Use it: when asked."\n---\nx\n')
    assert meta == {"name": "a", "description": "Use it: when asked."}


@pytest.mark.parametrize(
    ("folder", "head", "message"),
    [
        ("a", "name: a\n", "description"),
        ("a", "description: d\n", "name"),
        ("a", "name: a\ndescription: d\nallowed-tools: Bash\n", "portable"),
        ("b", "name: a\ndescription: d\n", "match its folder"),
        ("Ab", "name: Ab\ndescription: d\n", "lower-case"),
        ("a--b", "name: a--b\ndescription: d\n", "lower-case"),
        ("-a", "name: -a\ndescription: d\n", "lower-case"),
        ("a" * 65, f"name: {'a' * 65}\ndescription: d\n", "64"),
        ("a", "name: a\ndescription: " + "d" * 1025 + "\n", "1024"),
        ("a", "name: a\nname: a\ndescription: d\n", "twice"),
        ("a", "name: a\ndescription: >\n  folded\n", "single line"),
        ("a", "name: a\ndescription: Use it: when asked.\n", "quote"),
        ("a", "name: a\ndescription: [list]\n", "quote"),
    ],
)
def test_non_portable_frontmatter_is_rejected(
    tmp_path: Path, folder: str, head: str, message: str
) -> None:
    with pytest.raises(SkillError, match=message):
        load_skill(_skill(tmp_path, folder, head))


def test_a_skill_without_frontmatter_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a"
    path.mkdir()
    (path / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")
    with pytest.raises(SkillError, match="frontmatter"):
        load_skill(path)


def test_framework_skills_are_all_portable() -> None:
    skills = framework_skills()
    assert "devloop-setup" in skills
    for name, skill in skills.items():
        assert skill.name == name
