"""Read the rules that judge a pull request from its base, not from the pull request.

``devloop changes``, ``devloop size`` and ``devloop changelog check`` decide from
``devloop.toml`` whether a pull request is documents-only, within the size limit and
has valid changelog fragments. In CI the working tree is the pull request's merge, so
its ``devloop.toml`` is the pull request's own. With ``--config-from <rev>`` these
commands read the file from the base commit instead:

- the file is read as a blob from the Git object store (``cat-file``), so no filter,
  text conversion or other program the pull request names is run, and no inherited
  ``GIT_*`` variable reaches git;
- a base with no ``devloop.toml`` (the pull request that adopts devloop) falls back to
  the pull request's file, and the output says so;
- anything else (a revision that is not there, a symlink, text that is not UTF-8 or not
  a valid configuration) is an error: the check fails, it never falls back.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from devloop.config import CONFIG_FILE, Config, ConfigError, load_config, parse_config
from devloop.git import git

_REGULAR_FILE_MODES = frozenset({"100644", "100755"})


def _base_blob(root: Path, rev: str) -> str | None:
    """Return the text of ``devloop.toml`` at ``rev``, or None when ``rev`` has no such file.

    Raise ``ConfigError`` when ``rev`` is not a commit here or the file is not a regular
    UTF-8 text file.
    """
    where = f"the base's {CONFIG_FILE} ({rev})"
    if not rev or rev.startswith("-"):
        raise ConfigError(f"cannot read {where}: {rev!r} is not a revision")
    try:
        commit = git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").strip()
        entry = git(root, "--literal-pathspecs", "ls-tree", "-z", commit, "--", CONFIG_FILE)
        if not entry:
            return None
        mode, kind = entry.split("\t", 1)[0].split(" ")[:2]
        if kind != "blob" or mode not in _REGULAR_FILE_MODES:
            raise ConfigError(f"cannot read {where}: it is not a regular file (mode {mode})")
        return git(root, "cat-file", "blob", f"{commit}:{CONFIG_FILE}")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or "is the base commit fetched?"
        raise ConfigError(f"cannot read {where}: git failed ({detail})") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {where}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"cannot read {where}: it is not UTF-8 text") from exc


def base_config(root: Path, rev: str) -> tuple[Config, str]:
    """Return (the configuration at ``rev``, a note), for judging a pull request by its base.

    The note is empty unless ``rev`` has no ``devloop.toml``: then the configuration is
    the working tree's (the pull request's own) and the note says so. Every other
    failure raises ``ConfigError``.
    """
    text = _base_blob(root, rev)
    if text is None:
        note = (
            f"devloop: the base ({rev}) has no {CONFIG_FILE}, so this run uses the "
            "pull request's own rules."
        )
        return load_config(root / CONFIG_FILE), note
    try:
        return parse_config(text), ""
    except ConfigError as exc:
        raise ConfigError(f"the base's {CONFIG_FILE} ({rev}) is not valid: {exc}") from exc
