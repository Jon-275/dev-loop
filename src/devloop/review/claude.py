"""The Anthropic (Claude) reviewer, through the official SDK: ``pip install dev-loop[anthropic]``.

The request streams (a long review can exceed a plain request's timeout) and returns the
final message. ``output_config`` asks for JSON in the review schema at the run's effort;
thinking is adaptive by default on current models. ``fallbacks: "default"`` lets the API
re-run a declined request on a fallback model inside the same call. The SDK's own retry
is off (``max_retries=0``), so devloop's backoff is the only retry and the job budget holds.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from devloop.review.http import is_network_error
from devloop.review.model import (
    TRANSIENT_STATUS,
    Effort,
    Provider,
    ReplyCutOff,
    ReviewError,
    Usage,
    token_count,
)

#: The server-side fallback beta: a declined request is re-run on a fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
#: Anthropic's "overloaded" status, retried like a 503.
OVERLOADED = 529
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
#: Error types worth retrying when the API reports them inside a stream (status 200).
STREAM_RETRY_TYPES = frozenset({"overloaded_error", "api_error", "rate_limit_error"})
#: The clock a request's deadline is measured on.
_now = time.monotonic

#: Send one request's keyword arguments and return the final message.
Stream = Callable[[dict[str, Any]], object]


def is_transient(error: BaseException) -> bool:
    """Return whether retrying an Anthropic error may succeed.

    A timeout or lost connection, or a 408, 429, 5xx or 529 status, unless the response
    says ``x-should-retry: false``. A used-up credit balance is a 400, so never retried.
    """
    import anthropic

    if isinstance(error, anthropic.APIConnectionError):  # APITimeoutError included
        return True
    import httpx2

    if isinstance(error, anthropic.APIStatusError):
        if error.response.headers.get("x-should-retry", "").strip().lower() == "false":
            return False
        if error.status_code < 400:  # an error event inside a stream: judge it by its type
            return _error_type(error) in STREAM_RETRY_TYPES
        return error.status_code in TRANSIENT_STATUS | {OVERLOADED}
    if isinstance(error, httpx2.TransportError):  # the connection failed while reading
        return True
    return is_network_error(error)


def _error_type(error: object) -> str | None:
    """Return the API error's ``error.type`` from its body, if it has one."""
    body = getattr(error, "body", None)
    found = body.get("error") if isinstance(body, dict) else None
    kind = found.get("type") if isinstance(found, dict) else None
    return kind if isinstance(kind, str) else None


def describe(error: BaseException) -> str:
    """Return a log-safe name for ``error``: its status and error type, or its class."""
    import anthropic

    if isinstance(error, anthropic.APIStatusError):
        kind = _error_type(error)
        named = f" {kind}" if kind is not None and _SAFE_CODE.fullmatch(kind) else ""
        if error.status_code < 400:
            return f"stream error{named}"
        return f"HTTP {error.status_code}{named}"
    return type(error).__name__


def api_errors() -> tuple[type[BaseException], ...]:
    """Return the errors a request can end in: the SDK's API errors and network errors."""
    import anthropic
    import httpx2

    return (
        anthropic.APIError,
        httpx2.TransportError,
        httpx2.DecodingError,
        json.JSONDecodeError,
        OSError,
    )


#: The endpoint the SDK uses unless the reviewer's config names another.
API_URL = "https://api.anthropic.com"


def sdk_stream(api_key: str, timeout: float, base_url: str = "") -> Stream:
    """Return a sender that streams one request through the SDK and returns its message.

    The endpoint is passed explicitly, so ``ANTHROPIC_BASE_URL`` in the environment
    cannot send the key elsewhere.
    """
    import anthropic

    client = anthropic.Anthropic(
        api_key=api_key, base_url=base_url or API_URL, max_retries=0, timeout=timeout
    )

    def send(request: dict[str, Any]) -> object:
        """Stream the request and return the final message, within ``timeout`` in all.

        The SDK's timeout bounds each read, and a busy stream keeps resetting it, so the
        whole request gets its own deadline: past it the stream is closed.

        Raises:
            TimeoutError: the request ran past its deadline (retried like a timeout).

        """
        deadline = _now() + timeout
        with client.beta.messages.stream(**request) as stream:
            for _ in stream:
                if _now() > deadline:
                    raise TimeoutError(f"no final message within {timeout:.0f} s")
            return stream.get_final_message()

    return send


def request(
    model: str, max_tokens: int, prompt: str, effort: Effort, schema: dict[str, Any]
) -> dict[str, Any]:
    """Return the keyword arguments of one review request."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
        "system": "You are a code reviewer. Reply with JSON only.",
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}, "effort": effort},
    }


def reply_text(provider: Provider, message: object, usage: Usage) -> str:
    """Return the review text of a final message, counting its tokens.

    Input is uncached plus cached tokens; output includes thinking.

    Raises:
        ReplyCutOff: the reply stopped at ``max_tokens``.
        ReviewError: the model and its fallback declined, or there is no text.

    """
    counts = getattr(message, "usage", None)
    given = token_count(getattr(counts, "input_tokens", None))
    made = token_count(getattr(counts, "output_tokens", None))
    cached = [
        token_count(getattr(counts, n, 0) or 0)
        for n in ("cache_read_input_tokens", "cache_creation_input_tokens")
    ]
    if given is None or made is None or None in cached:
        usage.add(None, None)
    else:
        usage.add(given + sum(c for c in cached if c is not None), made)
    stop = getattr(message, "stop_reason", None)
    if stop in ("max_tokens", "model_context_window_exceeded"):
        raise ReplyCutOff(f"{provider.name}'s reply was cut off at its limit (stop_reason: {stop})")
    if stop == "refusal":
        raise ReviewError(f"{provider.name} declined the review (stop_reason: refusal)")
    blocks = list(getattr(message, "content", None) or [])
    switches = [i for i, b in enumerate(blocks) if getattr(b, "type", "") == "fallback"]
    if switches:  # the declined model's partial text comes before the last switch
        blocks = blocks[switches[-1] + 1 :]
    text = "".join(str(getattr(b, "text", "")) for b in blocks if getattr(b, "type", "") == "text")
    if not text:
        raise ReviewError(f"{provider.name} returned no review text")
    return text
