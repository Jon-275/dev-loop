"""One JSON request to a model API with the standard library, and how to classify its errors.

A provider's error text can echo the request, and so the prompt; only the status and a
short error code are ever logged (:func:`describe`).
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from devloop.review.model import TRANSIENT_STATUS

#: An error code safe to log: a short identifier, never free text.
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


class ModelError(Exception):
    """A model API answered with an HTTP error status."""

    def __init__(self, status: int, body: object, headers: Mapping[str, str]) -> None:
        """Keep the status, the parsed error body and the response headers."""
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body
        self.headers = {k.lower(): v for k, v in headers.items()}

    def codes(self) -> list[str]:
        """Return the error's ``code``, ``type`` and ``status`` strings from its body."""
        error = self.body.get("error") if isinstance(self.body, dict) else None
        if not isinstance(error, dict):
            return []
        return [
            v
            for v in (error.get("code"), error.get("type"), error.get("status"))
            if isinstance(v, str)
        ]


def post_json(
    url: str, headers: Mapping[str, str], body: Mapping[str, Any], timeout: float
) -> dict[str, Any]:
    """Send ``body`` as JSON and return the JSON object the API answers with.

    Credentials go in ``headers``, which are never redirected to another host.

    Raises:
        ModelError: the API answered with an error status.
        TimeoutError, OSError: the request timed out or the connection failed.
        ValueError: the answer is not a JSON object.

    """
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    for name, value in headers.items():
        request.add_unredirected_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        text = error.read()
        try:
            parsed: object = json.loads(text) if text else None
        except ValueError:
            parsed = None
        raise ModelError(error.code, parsed, dict(error.headers.items())) from None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("the model API's answer is not a JSON object")
    return data


def is_network_error(error: BaseException) -> bool:
    """Return whether ``error`` is a timeout or a lost connection, worth retrying."""
    if isinstance(error, urllib.error.URLError) and not isinstance(error, urllib.error.HTTPError):
        return isinstance(error.reason, OSError)
    return isinstance(
        error,
        TimeoutError
        | socket.timeout
        | ConnectionError
        | http.client.RemoteDisconnected
        | http.client.IncompleteRead,
    )


def is_retryable_status(error: ModelError, extra: frozenset[int] = frozenset()) -> bool:
    """Return whether the status may succeed on retry, unless the API says not to retry."""
    if error.headers.get("x-should-retry", "").strip().lower() == "false":
        return False
    return error.status in TRANSIENT_STATUS | extra


def describe(error: BaseException) -> str:
    """Return a log-safe name for ``error``: the HTTP status and error code, or its type."""
    if isinstance(error, ModelError):
        code = next((c for c in error.codes() if _SAFE_CODE.fullmatch(c.lower())), None)
        return f"HTTP {error.status}" + (f" {code}" if code else "")
    return type(error).__name__
