"""The Anthropic reviewer, through the official SDK, against a fake stream."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from devloop import config as cfg
from devloop.review import claude, providers
from devloop.review.model import ATTEMPTS, ReplyCutOff, ReviewError, Usage

CLAUDE = providers.resolve("anthropic", {})
PROVIDER = providers.provider_for(CLAUDE, "lens")
REPLY = '{"summary": "s", "findings": [], "not_posted": []}'
URL = "https://api.anthropic.com/v1/messages"


def _message(text: str = REPLY, stop: str = "end_turn", **usage: object) -> SimpleNamespace:
    """Return a final message with one thinking and one text block."""
    counts = {"input_tokens": 100, "output_tokens": 40, **usage}
    return SimpleNamespace(
        stop_reason=stop,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(**counts),
    )


def _status(code: int, kind: str = "api_error", **headers: str) -> anthropic.APIStatusError:
    """Return an SDK status error, as the SDK builds it from a response."""
    response = httpx2.Response(code, headers=headers, request=httpx2.Request("POST", URL))
    body = {"type": "error", "error": {"type": kind, "message": "echoes PROMPT"}}
    return anthropic.APIStatusError("echoes PROMPT", response=response, body=body)


class Stream:
    """Answer each request with the next scripted message or error."""

    def __init__(self, *answers: object) -> None:
        """Keep the scripted answers."""
        self.answers = list(answers)
        self.sent: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> object:
        """Record the request and answer it."""
        self.sent.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _ask(stream: Stream, usage: Usage | None = None) -> providers.Ask:
    """Return an ask function that streams through ``stream`` and never sleeps."""
    return providers.asker(
        CLAUDE, PROVIDER, "key", usage or Usage(), stream=stream, sleep=lambda _: None
    )


def test_the_built_in_reviewer_is_claude_opus_with_its_own_status() -> None:
    assert (CLAUDE.model, CLAUDE.key, CLAUDE.max_output_tokens) == (
        "claude-opus-5-5",
        "ANTHROPIC_API_KEY",
        64_000,
    )
    assert (PROVIDER.status_context, PROVIDER.marker_prefix) == (
        "Anthropic review",
        "anthropic-finding",
    )
    own = providers.resolve("anthropic", {"anthropic": cfg.Reviewer(model="claude-sonnet-5-5")})
    assert own.model == "claude-sonnet-5-5"


def test_the_request_asks_for_strict_json_at_the_effort_with_fallbacks() -> None:
    stream, usage = Stream(_message(cache_read_input_tokens=900)), Usage()
    assert _ask(stream, usage)("PROMPT", "low") == REPLY
    [sent] = stream.sent
    assert sent["model"] == "claude-opus-5-5" and sent["max_tokens"] == 64_000
    assert sent["betas"] == [claude.FALLBACK_BETA] and sent["fallbacks"] == "default"
    assert sent["messages"] == [{"role": "user", "content": "PROMPT"}]
    output = sent["output_config"]
    assert output["effort"] == "low" and output["format"]["type"] == "json_schema"
    assert output["format"]["schema"]["additionalProperties"] is False
    assert "thinking" not in sent and "temperature" not in sent
    assert (usage.input_tokens, usage.output_tokens) == (1000, 40)


@pytest.mark.parametrize(
    ("message", "error", "match"),
    [
        (_message('{"summ', "max_tokens"), ReplyCutOff, "max_tokens"),
        (_message("", "refusal"), ReviewError, "declined the review"),
        (_message(""), ReviewError, "no review text"),
    ],
)
def test_an_incomplete_reply_is_a_review_error_and_is_still_counted(
    message: SimpleNamespace, error: type[Exception], match: str
) -> None:
    usage = Usage()
    with pytest.raises(error, match=match):
        _ask(Stream(message), usage)("p", "high")
    assert usage.requests == 1


def test_an_overload_or_timeout_is_retried_and_counted_as_failed() -> None:
    timeout = anthropic.APITimeoutError(request=httpx2.Request("POST", URL))
    stream, usage = Stream(_status(529, "overloaded_error"), timeout, _message()), Usage()
    assert _ask(stream, usage)("p", "high") == REPLY
    assert (len(stream.sent), usage.failed, usage.requests) == (3, 2, 1)


@pytest.mark.parametrize(
    ("error", "retried"),
    [
        (_status(529, "overloaded_error"), True),
        (_status(429, "rate_limit_error"), True),
        (_status(500), True),
        (_status(503, **{"x-should-retry": "false"}), False),
        (_status(400, "invalid_request_error"), False),
        (_status(401, "authentication_error"), False),
        (ConnectionResetError(), True),
        (ValueError("a bug"), False),
    ],
)
def test_only_errors_retrying_can_fix_are_retried(error: BaseException, retried: bool) -> None:
    assert PROVIDER.is_transient(error) is retried


def test_a_final_error_names_its_status_and_type_only() -> None:
    stream = Stream(*[_status(529, "overloaded_error")] * ATTEMPTS)
    with pytest.raises(ReviewError) as caught:
        _ask(stream)("p", "high")
    assert str(caught.value) == (
        f"Anthropic request failed after {ATTEMPTS} attempts: HTTP 529 overloaded_error"
    )


def test_a_bug_is_raised_as_itself_not_as_a_request_failure() -> None:
    with pytest.raises(ValueError, match="a bug"):
        _ask(Stream(ValueError("a bug")))("p", "high")


def test_without_the_sdk_the_reviewer_says_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(api_key: str, timeout: float, base_url: str = "") -> claude.Stream:
        """Fail as an install without the extra would."""
        raise ImportError("No module named 'anthropic'")

    monkeypatch.setattr(claude, "sdk_stream", missing)
    with pytest.raises(ReviewError, match=r"install dev-loop\[anthropic\]"):
        providers.asker(CLAUDE, PROVIDER, "key", Usage())


def test_the_sdk_sends_the_request_with_the_beta_header_and_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    seen: list[httpx2.Request] = []

    def refuse(request: httpx2.Request) -> httpx2.Response:
        """Record the request and refuse it as overloaded."""
        seen.append(request)
        return httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error"}})

    real = anthropic.Anthropic

    def client(
        *, api_key: str, base_url: str, max_retries: int, timeout: float
    ) -> anthropic.Anthropic:
        """Build the SDK client on a transport that never reaches the network."""
        http_client = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(refuse))
        return real(
            api_key=api_key,
            base_url=base_url,
            max_retries=max_retries,
            timeout=timeout,
            http_client=http_client,
        )

    monkeypatch.setattr(anthropic, "Anthropic", client)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://elsewhere.example")
    send = claude.sdk_stream("key", 5)
    with pytest.raises(anthropic.APIStatusError) as caught:
        send(claude.request("claude-opus-5-5", 64_000, "PROMPT", "high", {"type": "object"}))
    assert PROVIDER.is_transient(caught.value)
    [request] = seen  # max_retries=0: devloop's backoff is the only retry
    body = json.loads(request.content)
    assert request.headers["anthropic-beta"] == claude.FALLBACK_BETA
    assert request.headers["authorization"] == "Bearer key" and "key" not in str(request.url)
    assert request.url.host == "api.anthropic.com"
    assert body["stream"] is True and body["fallbacks"] == "default"
    assert body["output_config"]["effort"] == "high"
