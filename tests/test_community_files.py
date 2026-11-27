"""The files a public repository's visitors read: present, linked and pointing at real paths."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("SECURITY.md", "CONTRIBUTING.md", ".github/pull_request_template.md")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _read(name: str) -> str:
    """Return the text of a file under the repository root."""
    return (ROOT / name).read_text(encoding="utf-8")


def test_every_local_link_in_the_community_pages_resolves() -> None:
    """A link to a page that is not there helps nobody."""
    for name in PAGES:
        for link in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", _read(name)):
            if not link.startswith("http"):
                assert (ROOT / link).exists(), f"{name}: {link}"


def test_a_vulnerability_is_reported_in_private_and_never_by_email() -> None:
    """Contact is GitHub's private reporting for this repository, on every page that says so."""
    advisories = "https://github.com/Jon-275/dev-loop/security/advisories/new"
    assert advisories in _read("SECURITY.md")
    assert "Do not open a public issue" in _read("SECURITY.md")
    config = yaml.safe_load(_read(".github/ISSUE_TEMPLATE/config.yml"))
    assert [link["url"] for link in config["contact_links"]] == [advisories]
    assert "SECURITY.md" in _read("CONTRIBUTING.md")
    for name in (*PAGES, ".github/CODEOWNERS"):
        assert EMAIL.findall(_read(name)) == [], name


def test_contributing_says_when_a_pull_request_reaches_the_model_reviewers() -> None:
    """The label named is the one the review workflow waits for."""
    page = _read("CONTRIBUTING.md")
    shared = yaml.safe_load(_read(".github/workflows/review.yml"))
    trigger = shared["on"] if "on" in shared else shared[True]  # YAML 1.1: on is True
    label = trigger["workflow_call"]["inputs"]["approval_label"]["default"]
    assert f"`{label}`" in page
    assert "only after the maintainer has looked" in " ".join(page.split())
    for reviewer in ("Gemini", "Reflection"):
        assert reviewer in page


def test_every_code_owner_path_exists_and_the_workflows_are_owned() -> None:
    """A pattern for a path that moved would guard nothing."""
    lines = [
        line.split()
        for line in _read(".github/CODEOWNERS").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    paths = [path for path, *_ in lines]
    assert "/.github/" in paths and "/src/devloop/review/" in paths
    for path, *owners in lines:
        assert owners == ["@Jon-275"], path
        assert (ROOT / path.lstrip("/")).exists(), path


def test_the_issue_templates_are_valid_forms() -> None:
    """GitHub ignores a form it cannot parse, without saying so."""
    for name in ("bug_report.yml", "idea.yml"):
        form = yaml.safe_load(_read(f".github/ISSUE_TEMPLATE/{name}"))
        assert {"name", "description", "body"} <= set(form), name
        ids = [item["id"] for item in form["body"] if item["type"] != "markdown"]
        assert len(ids) == len(set(ids)) and ids, name
