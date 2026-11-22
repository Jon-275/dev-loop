"""Read a pull request's number and labels from the Actions event and the GitHub API.

The API is read first, because a re-run of a failed job reuses the original event
payload, which lacks a label added after the push. The payload is the fallback. The
token is sent only in the ``Authorization`` header, never in a URL or the output.
"""

from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping

API_TIMEOUT_SECONDS = 10
LABELS_PER_PAGE = 100
MAX_LABEL_PAGES = 10
# ``owner/name``; a part made only of dots (``..``) would climb out of ``/repos/``.
REPOSITORY = re.compile(r"(?!\.+/)[A-Za-z0-9_.-]+/(?!\.+$)[A-Za-z0-9_.-]+")

Fetch = Callable[[str, str], object]


class LabelReadError(Exception):
    """The pull request's current labels could not be read from the GitHub API."""


def pull_request(event_path: str | None) -> dict[str, object] | None:
    """Return the event payload's ``pull_request`` object, or None if unreadable or absent."""
    if not event_path:
        return None
    try:
        with open(event_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    pr = payload.get("pull_request") if isinstance(payload, dict) else None
    return pr if isinstance(pr, dict) else None


def payload_labels(event_path: str | None) -> set[str] | None:
    """Return the label names in the event payload, or None if unreadable."""
    pr = pull_request(event_path)
    found = None if pr is None else pr.get("labels")
    if not isinstance(found, list):
        return None
    return {x["name"] for x in found if isinstance(x, dict) and isinstance(x.get("name"), str)}


def pr_number(event_path: str | None) -> int | None:
    """Return the pull request's number from the event payload, or None if absent.

    A PR's number never changes, so a re-run's stale payload still names the right one.
    """
    pr = pull_request(event_path)
    number = None if pr is None else pr.get("number")
    if isinstance(number, int) and not isinstance(number, bool) and number > 0:
        return number
    return None


def get_json(url: str, token: str) -> object:
    """GET ``url`` with ``token`` as an unredirected header and return the parsed body.

    If the API redirects, urllib follows without the token, so it never reaches
    another host.
    """
    request = urllib.request.Request(  # noqa: S310 - api_labels allows only https
        url,
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    )
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:  # noqa: S310
        body: object = json.loads(response.read().decode("utf-8"))
    return body


def api_labels(
    repository: str, number: int, token: str, api_url: str, fetch: Fetch = get_json
) -> set[str]:
    """Return the pull request's current label names from the GitHub API.

    Raises ``LabelReadError`` with the reason (never the token or a response body)
    for an unusable setting, a network or HTTP error, or an answer that is not labels.
    """
    if not token:
        raise LabelReadError("no GITHUB_TOKEN")
    if not REPOSITORY.fullmatch(repository):
        raise LabelReadError("no usable GITHUB_REPOSITORY")
    if urllib.parse.urlsplit(api_url).scheme != "https":
        raise LabelReadError("GITHUB_API_URL is not https")
    base = f"{api_url.rstrip('/')}/repos/{repository}/issues/{number}/labels"
    names: set[str] = set()
    # One page past the cap: an empty page there means every label was read.
    for page in range(1, MAX_LABEL_PAGES + 2):
        try:
            body = fetch(f"{base}?per_page={LABELS_PER_PAGE}&page={page}", token)
        except urllib.error.HTTPError as error:
            raise LabelReadError(f"HTTP {error.code}") from None
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
            raise LabelReadError(type(error).__name__) from None
        if not isinstance(body, list) or not all(
            isinstance(x, dict) and isinstance(x.get("name"), str) for x in body
        ):
            raise LabelReadError("the answer is not a list of labels")
        names.update(x["name"] for x in body)
        if len(body) < LABELS_PER_PAGE:
            return names
    raise LabelReadError(f"more than {MAX_LABEL_PAGES} pages of labels")


def current_labels(
    event_path: str | None, env: Mapping[str, str], fetch: Fetch = get_json
) -> tuple[set[str] | None, str]:
    """Return (label names, a line naming their source): the API first, the payload on failure.

    The names are None only when neither source could be read.
    """
    number = pr_number(event_path)
    try:
        if number is None:
            raise LabelReadError("no pull request number in the event payload")
        names = api_labels(
            env.get("GITHUB_REPOSITORY", ""),
            number,
            env.get("GITHUB_TOKEN", ""),
            env.get("GITHUB_API_URL") or "https://api.github.com",
            fetch,
        )
    except LabelReadError as error:
        fallback = payload_labels(event_path)
        state = (
            "which are unreadable too" if fallback is None else "which a re-run does not refresh"
        )
        return fallback, (
            f"Labels: the GitHub API read failed ({error}); "
            f"using the event payload's labels, {state}."
        )
    return names, "Labels: read from the GitHub API when the check ran."
