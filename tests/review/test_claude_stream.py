"""The Anthropic reviewer over the real SDK and a mock transport: streams that go wrong."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import anthropic
import httpx2
import pytest

from devloop import config as cfg
from devloop.review import claude, providers
from devloop.review.model import ReviewError, Usage

CLAUDE = providers.resolve("anthropic", {})
PROVIDER = providers.provider_for(CLAUDE, "lens")
REPLY = '{"summary": "s", "findings": [], "not_posted": []}'
Event = tuple[str, dict[str, object]]
START: Event = (
    "message_start",
    {
        "type": "message_start",
        "message": {
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1},
        },
    },
)


def _sse(*events: Event) -> bytes:
    """Return server-sent events."""
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


def _text(index: int, text: str) -> list[Event]:
    """Return the events of one text block."""
    return [
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": index}),
    ]


END: list[Event] = [
    (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 50},
        },
    ),
    ("message_stop", {"type": "message_stop"}),
]


def _serve(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx2.Request], httpx2.Response]
) -> None:
    """Make the SDK client answer every request with ``handler``."""
    real = anthropic.Anthropic

    def client(
        *, api_key: str, base_url: str, max_retries: int, timeout: float
    ) -> anthropic.Anthropic:
        """Build the SDK client on a mock transport."""
        http_client = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
        return real(
            api_key=api_key,
            base_url=base_url,
            max_retries=max_retries,
            timeout=timeout,
            http_client=http_client,
        )

    monkeypatch.setattr(anthropic, "Anthropic", client)


def _stream(body: bytes | httpx2.SyncByteStream) -> httpx2.Response:
    """Return an event-stream response."""
    headers = {"content-type": "text/event-stream"}
    if isinstance(body, bytes):
        return httpx2.Response(200, headers=headers, content=body)
    return httpx2.Response(200, headers=headers, stream=body)


def _ask(usage: Usage, timeout: float = 300) -> providers.Ask:
    """Return an ask function on the real SDK that never sleeps."""
    return providers.asker(CLAUDE, PROVIDER, "key", usage, sleep=lambda _: None, timeout=timeout)


def test_an_overload_reported_inside_the_stream_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [
        _sse(
            START,
            ("error", {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}),
        ),
        _sse(START, *_text(0, REPLY), *END),
    ]
    _serve(monkeypatch, lambda request: _stream(answers.pop(0)))
    usage = Usage()
    assert _ask(usage)("p", "high") == REPLY
    assert usage.failed == 1


class _Reset(httpx2.SyncByteStream):
    """A stream whose connection resets after the first event."""

    def __iter__(self) -> Iterator[bytes]:
        """Yield the first event, then fail."""
        yield _sse(START)
        raise httpx2.ReadError("peer reset")


def test_a_connection_lost_mid_stream_is_retried_then_a_review_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, lambda request: _stream(_Reset()))
    usage = Usage()
    with pytest.raises(ReviewError, match="after 3 attempts: ReadError"):
        _ask(usage)("p", "high")
    assert usage.failed == 3


def test_a_malformed_stream_is_a_review_error(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _sse(START) + b"event: content_block_start\ndata: {not json\n\n"
    _serve(monkeypatch, lambda request: _stream(body))
    with pytest.raises(ReviewError, match="JSONDecodeError"):
        _ask(Usage())("p", "high")


def test_after_a_fallback_only_the_fallback_models_text_is_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    switch: list[Event] = [
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "fallback",
                    "from": {"model": "claude-opus-5-5"},
                    "to": {"model": "claude-sonnet-5-5"},
                    "trigger": {"type": "refusal"},
                },
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
    ]
    body = _sse(START, *_text(0, '{"summary": "par'), *switch, *_text(2, REPLY), *END)
    _serve(monkeypatch, lambda request: _stream(body))
    assert _ask(Usage())("p", "high") == REPLY


class _Slow(httpx2.SyncByteStream):
    """A busy stream: a thousand small deltas, each read well within the read timeout."""

    def __iter__(self) -> Iterator[bytes]:
        """Yield the start, then many deltas, then the end."""
        yield _sse(START, _text(0, "")[0])
        for _ in range(1000):
            delta = {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": " "},
            }
            yield _sse(("content_block_delta", delta))
        yield _sse(("content_block_stop", {"type": "content_block_stop", "index": 0}), *END)


def test_one_request_is_bounded_by_its_timeout_even_while_the_stream_is_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter(float(n) for n in range(10**6))
    monkeypatch.setattr(claude, "_now", lambda: next(clock))
    _serve(monkeypatch, lambda request: _stream(_Slow()))
    with pytest.raises(ReviewError, match="after 3 attempts: TimeoutError"):
        _ask(Usage(), timeout=50)("p", "high")


def test_a_reply_cut_off_by_the_context_window_is_split_like_max_tokens() -> None:
    from types import SimpleNamespace

    from devloop.review.model import ReplyCutOff

    message = SimpleNamespace(stop_reason="model_context_window_exceeded", content=[], usage=None)
    with pytest.raises(ReplyCutOff, match="model_context_window_exceeded"):
        claude.reply_text(PROVIDER, message, Usage())


def test_a_custom_anthropic_reviewer_gets_the_anthropic_output_limit() -> None:
    own = {"sonnet": cfg.Reviewer(kind="anthropic", model="claude-sonnet-5-5")}
    assert providers.resolve("sonnet", own).max_output_tokens == 64_000
