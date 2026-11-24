"""Tests for ``devloop sync``: one source rendered into every tool's location."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from devloop.config import PR, Agents, Config, Skills
from devloop.sync import IGNORE, MANIFEST, SyncError, apply, plan

CFG = Config.default(version="0.1")
TARGETS = (".claude/skills", ".agents/skills", ".github/skills")


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project_skill(root: Path, name: str, body: str = "Steps.\n") -> None:
    _write(
        root, f"devloop/skills/{name}/SKILL.md", f"---\nname: {name}\ndescription: d\n---\n{body}"
    )


def test_sync_on_an_empty_project_writes_every_tool_layout(tmp_path: Path) -> None:
    changes = apply(tmp_path, CFG)
    assert {c.action for c in changes} == {"create"}
    assert (tmp_path / "AGENTS.md").is_file()
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"
    settings = json.loads((tmp_path / ".gemini/settings.json").read_text(encoding="utf-8"))
    assert settings == {"context": {"fileName": ["AGENTS.md", "GEMINI.md"]}}
    for target in TARGETS:
        assert (tmp_path / target / "devloop-setup" / "SKILL.md").is_file()
    assert (tmp_path / MANIFEST).is_file()
    assert plan(tmp_path, CFG) == []


def test_agents_hand_off_notes_are_never_committed(tmp_path: Path) -> None:
    """AGENTS.md says notes go in .devloop/notes/ and are never committed; git agrees."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    apply(tmp_path, CFG)
    assert (tmp_path / IGNORE).read_text(encoding="utf-8") == "notes/\n"
    note = tmp_path / ".devloop" / "notes" / "fix-x.md"
    note.parent.mkdir()
    note.write_text("hand-off\n", encoding="utf-8")
    ignored = subprocess.run(
        ["git", "-C", str(tmp_path), "check-ignore", "-q", str(note)], check=False
    )
    assert ignored.returncode == 0
    (tmp_path / IGNORE).unlink()
    assert [c.path for c in plan(tmp_path, CFG)] == [IGNORE]


def test_sync_is_idempotent(tmp_path: Path) -> None:
    apply(tmp_path, CFG)
    assert apply(tmp_path, CFG) == []


def test_project_skills_render_alongside_and_retire_cleanly(tmp_path: Path) -> None:
    _project_skill(tmp_path, "our-flow")
    apply(tmp_path, CFG)
    for target in TARGETS:
        assert (tmp_path / target / "our-flow" / "SKILL.md").is_file()
    (tmp_path / "devloop/skills/our-flow/SKILL.md").unlink()
    (tmp_path / "devloop/skills/our-flow").rmdir()
    changes = apply(tmp_path, CFG)
    assert {c.action for c in changes} == {"delete", "update"}  # copies, and the manifest
    for target in TARGETS:
        assert not (tmp_path / target / "our-flow").exists()


def test_an_edited_copy_is_drift_and_sync_restores_the_source(tmp_path: Path) -> None:
    apply(tmp_path, CFG)
    copy = tmp_path / ".claude/skills/devloop-setup/SKILL.md"
    source = copy.read_bytes()
    copy.write_text("hand edit\n", encoding="utf-8")
    assert [(c.path, c.action) for c in plan(tmp_path, CFG)] == [
        (".claude/skills/devloop-setup/SKILL.md", "update")
    ]
    apply(tmp_path, CFG)
    assert copy.read_bytes() == source


def test_a_stray_file_in_a_rendered_skill_is_drift(tmp_path: Path) -> None:
    apply(tmp_path, CFG)
    _write(tmp_path, ".agents/skills/devloop-setup/extra.md", "x\n")
    assert [(c.path, c.action) for c in plan(tmp_path, CFG)] == [
        (".agents/skills/devloop-setup/extra.md", "delete")
    ]


def test_an_unmanaged_skill_is_never_overwritten(tmp_path: Path) -> None:
    _write(tmp_path, ".claude/skills/devloop-setup/SKILL.md", "hand kept copy\n")
    with pytest.raises(SyncError, match="not managed by devloop"):
        apply(tmp_path, CFG)
    assert not (tmp_path / "AGENTS.md").exists()


def test_skills_outside_devloop_in_a_target_are_left_alone(tmp_path: Path) -> None:
    _write(tmp_path, ".claude/skills/mine/SKILL.md", "---\nname: mine\ndescription: d\n---\n")
    apply(tmp_path, CFG)
    assert plan(tmp_path, CFG) == []
    assert (tmp_path / ".claude/skills/mine/SKILL.md").is_file()


def test_an_existing_claude_md_gets_the_import_and_keeps_its_text(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", "Claude-only notes.\n")
    apply(tmp_path, CFG)
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == (
        "@AGENTS.md\n\nClaude-only notes.\n"
    )
    _write(tmp_path, "CLAUDE.md", "Notes.\n\n@AGENTS.md\n")
    assert plan(tmp_path, CFG) == []


def test_gemini_settings_are_merged_not_replaced(tmp_path: Path) -> None:
    _write(tmp_path, ".gemini/settings.json", '{"theme": "x", "context": {"fileName": "CTX.md"}}')
    apply(tmp_path, CFG)
    data = json.loads((tmp_path / ".gemini/settings.json").read_text(encoding="utf-8"))
    assert data == {"theme": "x", "context": {"fileName": ["AGENTS.md", "CTX.md"]}}


def test_gemini_settings_that_already_load_agents_md_keep_their_bytes(tmp_path: Path) -> None:
    text = '{ "context": { "fileName": ["GEMINI.md", "AGENTS.md"] } }'
    _write(tmp_path, ".gemini/settings.json", text)
    apply(tmp_path, CFG)
    assert (tmp_path / ".gemini/settings.json").read_text(encoding="utf-8") == text


def test_invalid_gemini_settings_are_an_error(tmp_path: Path) -> None:
    _write(tmp_path, ".gemini/settings.json", "{not json")
    with pytest.raises(SyncError, match="settings.json"):
        plan(tmp_path, CFG)


def test_only_the_configured_tools_get_adapters(tmp_path: Path) -> None:
    cfg = replace(CFG, agents=Agents(tools=("codex",), skills_targets=(".agents/skills",)))
    apply(tmp_path, cfg)
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / ".gemini").exists()
    assert (tmp_path / ".agents/skills/devloop-setup/SKILL.md").is_file()


def test_agents_md_blocks_follow_the_config_and_project_text_stays(tmp_path: Path) -> None:
    apply(tmp_path, CFG)
    agents = tmp_path / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + "\nOur rule.\n", encoding="utf-8")
    apply(tmp_path, replace(CFG, pr=PR(max_lines=400)))
    text = agents.read_text(encoding="utf-8")
    assert "400 changed lines" in text
    assert "800 changed lines" not in text
    assert text.endswith("\nOur rule.\n")


@pytest.mark.parametrize(
    ("skills", "message"),
    [
        (Skills(include=("no-such-skill",)), "unknown framework skill"),
        (Skills(project=".claude/skills"), "skills target"),
    ],
)
def test_bad_skill_settings_are_errors(tmp_path: Path, skills: Skills, message: str) -> None:
    with pytest.raises(SyncError, match=message):
        plan(tmp_path, replace(CFG, skills=skills))


def test_a_project_skill_cannot_shadow_a_framework_skill(tmp_path: Path) -> None:
    _project_skill(tmp_path, "devloop-setup")
    with pytest.raises(SyncError, match="both"):
        plan(tmp_path, CFG)


def test_a_hand_kept_folder_with_extra_files_is_not_adopted(tmp_path: Path) -> None:
    apply(tmp_path, CFG)
    source = (tmp_path / ".claude/skills/devloop-setup/SKILL.md").read_bytes()
    (tmp_path / MANIFEST).unlink()
    _write(tmp_path, ".claude/skills/devloop-setup/notes.md", "mine\n")
    assert (tmp_path / ".claude/skills/devloop-setup/SKILL.md").read_bytes() == source
    with pytest.raises(SyncError, match="does not manage"):
        plan(tmp_path, CFG)


@pytest.mark.parametrize("entry", ["../outside.txt", "AGENTS.md", "<absolute>", "src/a.py"])
def test_a_manifest_entry_outside_the_skills_targets_is_never_deleted(
    tmp_path: Path, entry: str
) -> None:
    root = tmp_path / "p"
    apply(root, CFG)
    (tmp_path / "outside.txt").write_text("keep\n", encoding="utf-8")
    _write(root, "src/a.py", "keep\n")
    data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    if entry == "<absolute>":  # a real absolute path, but only ever one inside tmp_path
        entry = str(tmp_path / "outside.txt")
    (root / MANIFEST).write_text(json.dumps({"files": [*data["files"], entry]}))
    with pytest.raises(SyncError, match="not inside a skills target"):
        apply(root, CFG)
    assert (tmp_path / "outside.txt").is_file()
    assert (root / "AGENTS.md").is_file()
    assert (root / "src/a.py").is_file()


def test_retiring_a_skill_works_when_two_targets_are_one_folder(tmp_path: Path) -> None:
    (tmp_path / ".agents/skills").mkdir(parents=True)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/skills").symlink_to("../.agents/skills")
    _project_skill(tmp_path, "ours")
    apply(tmp_path, CFG)
    (tmp_path / "devloop/skills/ours/SKILL.md").unlink()
    (tmp_path / "devloop/skills/ours").rmdir()
    apply(tmp_path, CFG)
    assert not (tmp_path / ".agents/skills/ours").exists()
    assert plan(tmp_path, CFG) == []


def test_crlf_checkouts_are_not_drift(tmp_path: Path) -> None:
    apply(tmp_path, CFG)
    for rel in (".claude/skills/devloop-setup/SKILL.md", "AGENTS.md", "CLAUDE.md"):
        path = tmp_path / rel
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert plan(tmp_path, CFG) == []


def test_os_junk_in_a_rendered_skill_is_not_drift(tmp_path: Path) -> None:
    apply(tmp_path, CFG)
    _write(tmp_path, ".github/skills/devloop-setup/.DS_Store", "x")
    assert plan(tmp_path, CFG) == []
