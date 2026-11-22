"""What each supported coding agent reads: its instruction file and its skills folders.

Every value here was checked against the tool's own documentation; the sources are listed
per tool and in ``docs/tool-matrix.md``. Support changes fast, so re-check them on release.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    """One coding agent: how it finds ``AGENTS.md`` and where it discovers skills."""

    name: str
    title: str
    reads_agents_md: bool
    adapter: str | None
    skills_dirs: tuple[str, ...]
    sources: tuple[str, ...]


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            name="claude",
            title="Claude Code",
            # Reads AGENTS.md itself only when no CLAUDE.md exists; a CLAUDE.md that
            # imports it with ``@AGENTS.md`` works in every case and never loads it twice.
            reads_agents_md=False,
            adapter="CLAUDE.md",
            skills_dirs=(".claude/skills",),
            sources=(
                "https://code.claude.com/docs/en/memory",
                "https://code.claude.com/docs/en/skills",
            ),
        ),
        Tool(
            name="codex",
            title="Codex",
            reads_agents_md=True,
            adapter=None,
            skills_dirs=(".agents/skills",),
            sources=(
                "https://learn.chatgpt.com/docs/agent-configuration/agents-md",
                "https://learn.chatgpt.com/docs/build-skills",
            ),
        ),
        Tool(
            name="gemini",
            title="Gemini CLI",
            # Reads GEMINI.md unless ``context.fileName`` in .gemini/settings.json says
            # otherwise; its successor, Antigravity CLI, reads AGENTS.md natively.
            reads_agents_md=False,
            adapter=".gemini/settings.json",
            skills_dirs=(".agents/skills", ".gemini/skills"),
            sources=(
                "https://geminicli.com/docs/cli/gemini-md/",
                "https://geminicli.com/docs/cli/skills/",
                "https://antigravity.google/docs/cli/gcli-migration.md",
            ),
        ),
        Tool(
            name="cursor",
            title="Cursor",
            reads_agents_md=True,
            adapter=None,
            skills_dirs=(".agents/skills", ".cursor/skills", ".claude/skills"),
            sources=(
                "https://cursor.com/docs/context/rules",
                "https://cursor.com/docs/context/skills",
            ),
        ),
        Tool(
            name="copilot",
            title="GitHub Copilot",
            # The cloud agent and CLI also read .agents/skills and .claude/skills, but
            # Copilot in VS Code reads only .github/skills, so that folder is required.
            reads_agents_md=True,
            adapter=None,
            skills_dirs=(".github/skills",),
            sources=(
                "https://docs.github.com/en/copilot/reference/custom-instructions-support",
                "https://docs.github.com/en/copilot/concepts/agents/about-agent-skills",
                "https://code.visualstudio.com/docs/copilot/customization/agent-skills",
            ),
        ),
    )
}


def default_skills_targets(tools: tuple[str, ...]) -> tuple[str, ...]:
    """Return the fewest folders that give every tool in ``tools`` a skills folder it reads.

    Each tool's first listed folder is its preferred one; a tool already served by an
    earlier choice adds nothing.
    """
    targets: list[str] = []
    for name in tools:
        tool = TOOLS[name]
        if not any(d in targets for d in tool.skills_dirs):
            targets.append(tool.skills_dirs[0])
    return tuple(targets)
