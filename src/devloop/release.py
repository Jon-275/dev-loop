"""``devloop release-notes`` and ``devloop release-due``: what a release says, and when one is due.

A release is a tag ``v<version>`` on the commit that set that version on the default
branch, and a published release whose notes are that version's changelog section. The
release workflow asks ``devloop release-due`` on every push to the default branch and
creates the tag and the release only when the answer is yes.

States of a pushed commit:

- **not due**: the version is the one its first parent has, or the tag ``v<version>``
  exists. Nothing is created.
- **due**: the commit changed the version, no tag has that version, and the changelog
  has its section.
- **refused**: the commit changed the version, but the version is not ``X.Y.Z`` or the
  changelog has no section for it. Nothing is created and the run fails.

Invariants:

1. Only the commit that changed the version can be due, so a later push never tags a
   later commit with a version an earlier one set.
2. A version whose tag exists is never due, so an existing tag is never moved and a
   second run for the same commit creates nothing.
3. A commit is due only together with its notes: no changelog section, no release.
4. Only a plain ``X.Y.Z`` version is released. A pre-release, a ``v`` prefix or any
   other spelling is refused.
5. Whatever cannot be read (the tags, the parent commit, ``pyproject.toml``) is an
   error, never "not due" and never "due".
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from devloop.config import CONFIG_FILE, load_config
from devloop.git import file_at, git

#: A released version: three numbers, none with a leading zero.
VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
PYPROJECT = "pyproject.toml"


class ReleaseError(ValueError):
    """The release is refused: a version that is not ``X.Y.Z``, or no changelog section."""


class ReadError(Exception):
    """Something the decision needs could not be read, so there is no decision."""


@dataclass(frozen=True)
class Release:
    """A release that is due: its version, its tag and its notes."""

    version: str
    tag: str
    notes: str


def check_version(version: str) -> str:
    """Return ``version`` when it is a plain ``X.Y.Z``.

    Raises:
        ReleaseError: it is a pre-release, has a prefix or suffix, or is malformed.

    """
    if not VERSION.fullmatch(version):
        raise ReleaseError(
            f"{version!r} is not a version to release: it must be X.Y.Z, three numbers "
            "with no prefix, suffix or pre-release part"
        )
    return version


def notes(changelog: str, version: str) -> str:
    """Return the body of the ``## <version> - <date>`` section of ``changelog``.

    The body runs from the line after the heading to the next line that starts with
    ``## ``, without the blank lines around it, and ends with one newline.

    Raises:
        ReleaseError: ``version`` is not ``X.Y.Z``, the changelog has no such section
            or more than one, or the section is empty.

    """
    check_version(version)
    heading = re.compile(rf"## {re.escape(version)} - \d{{4}}-\d{{2}}-\d{{2}}\s*")
    lines = changelog.splitlines()
    starts = [index for index, line in enumerate(lines) if heading.fullmatch(line)]
    if not starts:
        raise ReleaseError(f"the changelog has no '## {version} - <YYYY-MM-DD>' section")
    if len(starts) > 1:
        raise ReleaseError(f"the changelog has {len(starts)} sections for {version}")
    body: list[str] = []
    for line in lines[starts[0] + 1 :]:
        if line.startswith("## "):
            break
        body.append(line.rstrip())
    text = "\n".join(body).strip("\n")
    if not text.strip():
        raise ReleaseError(f"the changelog's section for {version} is empty")
    return text + "\n"


def unreleased(version: str, tags: Collection[str]) -> bool:
    """Return whether no tag in ``tags`` is ``v<version>``."""
    return f"v{version}" not in tags


def due(
    version: str, previous: str | None, tags: Collection[str], changelog: str
) -> Release | None:
    """Return the release due for a commit, or None when nothing is due.

    Args:
        version: The version the commit records.
        previous: The version its first parent records, or None when it records none.
        tags: Every tag of the repository.
        changelog: The commit's changelog.

    Raises:
        ReleaseError: the commit changed the version to one that is not ``X.Y.Z``, or
            to one the changelog has no section for.

    """
    if version == previous:
        return None
    check_version(version)
    if not unreleased(version, tags):
        return None
    return Release(version, f"v{version}", notes(changelog, version))


def project_version(pyproject: str) -> str | None:
    """Return ``project.version`` of a ``pyproject.toml``'s text, or None when it has none.

    Raises:
        ReadError: the text is not valid TOML.

    """
    try:
        data = tomllib.loads(pyproject)
    except tomllib.TOMLDecodeError as error:
        raise ReadError(f"{PYPROJECT} is not valid TOML: {error}") from None
    project = data.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    return version if isinstance(version, str) and version else None


def remote_tags(root: Path, remote: str = "origin") -> set[str]:
    """Return the names of every tag ``remote`` has, asked of the remote itself.

    Raises:
        ReadError: the remote could not be read.

    """
    try:
        listing = git(root, "ls-remote", "--tags", remote)
    except (OSError, subprocess.CalledProcessError) as error:
        reason = getattr(error, "stderr", "") or str(error)
        raise ReadError(f"could not read the tags of {remote}: {reason.strip()}") from None
    prefix = "refs/tags/"
    refs = (line.split("\t")[-1] for line in listing.splitlines())
    return {ref[len(prefix) :].removesuffix("^{}") for ref in refs if ref.startswith(prefix)}


def _read(path: Path) -> str:
    """Return the text of ``path``; a file that cannot be read is a :class:`ReadError`."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ReadError(f"{path.name} cannot be read: {error}") from None


def _versions(root: Path) -> tuple[str, str | None]:
    """Return the version HEAD records and the one its first parent records (or None).

    Raises:
        ReadError: HEAD records no version, or its first parent cannot be read, as in
            a checkout one commit deep.

    """
    version = project_version(_read(root / PYPROJECT))
    if version is None:
        raise ReadError(f"{PYPROJECT} has no project.version")
    try:
        git(root, "rev-parse", "--verify", "--quiet", "HEAD^1^{commit}")
    except (OSError, subprocess.CalledProcessError):
        raise ReadError(
            "HEAD's first parent cannot be read (check out at least two commits deep)"
        ) from None
    try:
        previous = project_version(file_at(root, "HEAD^1", PYPROJECT))
    except ReadError:
        previous = None  # the parent's file is broken, so this commit changed the version
    return version, previous


def _write_outputs(path: str, values: Mapping[str, str]) -> None:
    """Append ``values`` to the GitHub Actions output file at ``path``, or print them."""
    lines = [f"{key}={value}\n" for key, value in values.items()]
    if path:
        with open(path, "a", encoding="utf-8") as out:
            out.writelines(lines)
    else:
        sys.stdout.writelines(lines)


def main_notes(root: Path, version: str | None) -> int:
    """Print the changelog section of ``version`` (default: the project's); 1 when refused."""
    try:
        changelog = _read(root / load_config(root / CONFIG_FILE).changelog.file)
        chosen = version if version is not None else project_version(_read(root / PYPROJECT))
        if chosen is None:
            raise ReadError(f"{PYPROJECT} has no project.version; pass --version")
        print(notes(changelog, chosen), end="")
    except ReleaseError as error:
        print(f"devloop release-notes: {error}", file=sys.stderr)
        return 1
    except ReadError as error:
        print(f"devloop release-notes: {error}", file=sys.stderr)
        return 2
    return 0


def main_due(root: Path, output: Path, environ: Mapping[str, str]) -> int:
    """Say whether HEAD is a release to tag, and write its notes to ``output`` when it is.

    Writes ``release`` (``true`` or ``false``) and, when true, ``version`` and ``tag`` to
    ``$GITHUB_OUTPUT``. Exit code 0 is an answer, 1 a refused release, 2 could not check;
    ``release=true`` is written only with exit code 0, after the notes.
    """
    outputs = environ.get("GITHUB_OUTPUT", "")
    try:
        version, previous = _versions(root)
        if version == previous:
            print(f"Nothing to release: this commit did not change the version ({version}).")
            _write_outputs(outputs, {"release": "false"})
            return 0
        changelog = _read(root / load_config(root / CONFIG_FILE).changelog.file)
        release = due(version, previous, remote_tags(root), changelog)
        if release is None:
            print(f"Nothing to release: the tag v{version} exists.")
            _write_outputs(outputs, {"release": "false"})
            return 0
        output.write_text(release.notes, encoding="utf-8")
    except ReleaseError as error:
        print(f"::error title=Release::{error}; nothing was created", file=sys.stderr)
        return 1
    except (ReadError, OSError) as error:
        print(f"::error title=Release::could not check: {error}", file=sys.stderr)
        return 2
    print(f"Release {release.tag} is due on this commit (the version was {previous}).")
    _write_outputs(outputs, {"release": "true", "version": release.version, "tag": release.tag})
    return 0
