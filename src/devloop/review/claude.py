"""The Anthropic (Claude) reviewer, through the official SDK: ``pip install dev-loop[anthropic]``.

The request streams (a long review can exceed a plain request's timeout) and returns the
final message. ``output_config`` asks for JSON in the review schema at the run's effort;
thinking is adaptive by default on current models. ``fallbacks: "default"`` lets the API
re-run a declined request on a fallback model inside the same call. The SDK's own retry
is off (``max_retries=0``), so devloop's backoff is the only retry and the job budget holds.
"""

from __future__ import annotations

import re
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
    if isinstance(error, anthropic.APIStatusError):
        if error.response.headers.get("x-should-retry", "").strip().lower() == "false":
            return False
        return error.status_code in TRANSIENT_STATUS | {OVERLOADED}
    return is_network_error(error)


def describe(error: BaseException) -> str:
    """Return a log-safe name for ``error``: its status and error type, or its class."""
    import anthropic

    if isinstance(error, anthropic.APIStatusError):
        body = error.body.get("error") if isinstance(error.body, dict) else None
        kind = body.get("type") if isinstance(body, dict) else None
        safe = isinstance(kind, str) and _SAFE_CODE.fullmatch(kind)
        return f"HTTP {error.status_code}" + (f" {kind}" if safe else "")
    return type(error).__name__


def api_errors() -> tuple[type[BaseException], ...]:
    """Return the errors a request can end in: the SDK's API errors and network errors."""
    import anthropic

    return (anthropic.APIError, OSError)


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
        """Stream the request and return the final message."""
        with client.beta.messages.stream(**request) as stream:
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
    if stop == "max_tokens":
        raise ReplyCutOff(
            f"{provider.name}'s reply was cut off at max_tokens (stop_reason: max_tokens)"
        )
    if stop == "refusal":
        raise ReviewError(f"{provider.name} declined the review (stop_reason: refusal)")
    blocks = getattr(message, "content", None) or []
    text = "".join(str(getattr(b, "text", "")) for b in blocks if getattr(b, "type", "") == "text")
    if not text:
        raise ReviewError(f"{provider.name} returned no review text")
    return text
