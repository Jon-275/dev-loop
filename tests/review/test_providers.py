"""The Gemini and OpenAI-compatible reviewers, against a fake HTTP sender."""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from collections.abc import Mapping
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from devloop import config as cfg
from devloop.review import http, providers
from devloop.review.model import ATTEMPTS, ReplyCutOff, ReviewError, Usage

GEMINI = providers.resolve("gemini", {})
REFLECTION = providers.resolve(
    "reflection", {"reflection": cfg.Reviewer(dimensions={"D1": "Revenue alignment"})}
)
REPLY = '{"summary": "s", "findings": [], "not_posted": []}'


class Sender:
    """Answer each request with the next scripted answer or error, recording what was sent."""

    def __init__(self, *answers: dict[str, Any] | BaseException) -> None:
        """Keep the scripted answers."""
        self.answers = list(answers)
        self.sent: list[tuple[str, Mapping[str, str], Mapping[str, Any], float]] = []

    def __call__(
        self, url: str, headers: Mapping[str, str], body: Mapping[str, Any], timeout: float
    ) -> dict[str, Any]:
        """Record the request and answer it."""
        self.sent.append((url, headers, body, timeout))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _ask(reviewer: providers.Reviewer, sender: Sender, usage: Usage | None = None) -> providers.Ask:
    """Return an ask function for ``reviewer`` that sends through ``sender`` and never sleeps."""
    provider = providers.provider_for(reviewer, providers.DEFAULT_LENS)
    return providers.asker(
        reviewer, provider, "key", usage or Usage(), send=sender, sleep=lambda _: None
    )


def _gemini(text: str = REPLY, finish: str = "STOP", **meta: object) -> dict[str, Any]:
    """Return a Gemini answer with one candidate."""
    parts = [{"text": "thinking...", "thought": True}, {"text": text}]
    return {
        "candidates": [{"content": {"parts": parts}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, **meta},
    }


def _chat(content: str | None = REPLY, finish: str = "stop", **message: object) -> dict[str, Any]:
    """Return an OpenAI-compatible chat completion."""
    return {
        "choices": [{"finish_reason": finish, "message": {"content": content, **message}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 30},
    }


def _error(status: int, body: object = None, **headers: str) -> http.ModelError:
    """Return a model API error."""
    return http.ModelError(status, body, headers)


def test_built_in_reviewers_keep_gtm_toolkits_names_and_markers() -> None:
    provider = providers.provider_for(GEMINI, "lens")
    assert (provider.name, provider.status_context, provider.marker_prefix) == (
        "Gemini",
        "Gemini review",
        "gemini-finding",
    )
    assert provider.summary_marker == "gemini-review" and provider.key_name == "GEMINI_API_KEY"
    tagged = providers.provider_for(REFLECTION, "lens")
    assert tagged.dimensions == {"D1": "Revenue alignment"} and "D1 tag" in tagged.finding_fields


def test_the_project_overrides_a_reviewer_and_defines_new_ones() -> None:
    own = {
        "gemini": cfg.Reviewer(model="gemini-5"),
        "local-llm": cfg.Reviewer(kind="openai", model="m", base_url="https://llm.example"),
    }
    assert providers.resolve("gemini", own).model == "gemini-5"
    llm = providers.resolve("local-llm", own)
    assert (llm.title, llm.key, llm.kind) == ("Local-llm", "LOCAL_LLM_API_KEY", "openai")
    with pytest.raises(ReviewError, match="unknown reviewer 'nobody'"):
        providers.resolve("nobody", own)
    with pytest.raises(ReviewError, match="needs model"):
        providers.resolve("openai", {})


def test_a_lens_comes_from_the_trusted_checkout_or_the_default(tmp_path: Path) -> None:
    (tmp_path / "lenses").mkdir()
    (tmp_path / "lenses" / "gemini.md").write_text("Project lens.\n")
    assert providers.lens_for(tmp_path, "lenses", "gemini") == "Project lens.\n"
    assert providers.lens_for(tmp_path, "lenses", "reflection") == providers.DEFAULT_LENS


def test_a_gemini_request_asks_for_json_at_the_effort_and_counts_thinking() -> None:
    sender, usage = Sender(_gemini(thoughtsTokenCount=50)), Usage()
    assert _ask(GEMINI, sender, usage)("PROMPT", "high") == REPLY
    [(url, headers, body, timeout)] = sender.sent
    assert url.endswith("/models/gemini-4-hydrogen:generateContent")
    assert headers == {"x-goog-api-key": "key"} and timeout == 300
    generation = body["generationConfig"]
    assert generation["thinkingConfig"] == {"thinkingLevel": "HIGH"}
    assert generation["responseJsonSchema"]["required"] == ["summary", "findings", "not_posted"]
    assert body["contents"][0]["parts"] == [{"text": "PROMPT"}]
    assert (usage.requests, usage.input_tokens, usage.output_tokens) == (1, 100, 70)


def test_a_gemini_reply_cut_off_at_its_limit_is_counted_then_split() -> None:
    usage = Usage()
    with pytest.raises(ReplyCutOff, match="MAX_TOKENS"):
        _ask(GEMINI, Sender(_gemini('{"summ', "MAX_TOKENS")), usage)("p", "low")
    assert usage.requests == 1


def test_a_gemini_overload_or_timeout_is_retried_but_an_exhausted_quota_is_not() -> None:
    usage = Usage()
    sender = Sender(_error(503), TimeoutError(), _gemini())
    assert _ask(GEMINI, sender, usage)("p", "low") == REPLY
    assert (len(sender.sent), usage.failed) == (3, 2)
    daily = {
        "error": {
            "details": [
                {
                    "@type": providers.QUOTA_FAILURE_TYPE,
                    "violations": [{"quotaId": "GenerateRequestsPerDayPerProject"}],
                }
            ]
        }
    }
    exhausted = Sender(_error(429, daily))
    with pytest.raises(ReviewError, match=r"^Gemini request failed: HTTP 429$"):
        _ask(GEMINI, exhausted)("p", "low")
    assert len(exhausted.sent) == 1
    assert providers.provider_for(GEMINI, "").is_transient(_error(429, {"error": {}}))


def test_a_lasting_outage_fails_after_every_attempt_and_names_no_details() -> None:
    sender = Sender(*[_error(503, {"error": {"message": "secret prompt"}})] * ATTEMPTS)
    with pytest.raises(ReviewError) as caught:
        _ask(GEMINI, sender)("p", "low")
    assert str(caught.value) == f"Gemini request failed after {ATTEMPTS} attempts: HTTP 503"


def test_an_openai_request_is_strict_json_with_its_reasoning_effort() -> None:
    sender, usage = Sender(_chat()), Usage()
    assert _ask(REFLECTION, sender, usage)("PROMPT", "low") == REPLY
    [(url, headers, body, _)] = sender.sent
    assert url == "https://api.reflection.ai/openai/v1/chat/completions"
    assert headers == {"Authorization": "Bearer key"}
    assert body["reasoning_effort"] == "low" and body["max_completion_tokens"] == 65_536
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True and schema["schema"]["additionalProperties"] is False
    assert (usage.input_tokens, usage.output_tokens) == (100, 30)


@pytest.mark.parametrize(
    ("answer", "error", "match"),
    [
        (_chat(finish="length"), ReplyCutOff, "65536-token output limit"),
        (_chat(finish="content_filter"), ReviewError, "content filter"),
        (_chat(None, refusal="I won't, because PROMPT"), ReviewError, "^Reflection refused"),
        (_chat(""), ReviewError, "no review text"),
        ({"choices": []}, ReviewError, "no choices"),
    ],
)
def test_an_incomplete_openai_reply_is_a_review_error(
    answer: dict[str, Any], error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match) as caught:
        _ask(REFLECTION, Sender(answer))("p", "high")
    assert "PROMPT" not in str(caught.value)


@pytest.mark.parametrize(
    ("failure", "retried"),
    [
        (_error(503), True),
        (_error(429, {"error": {"code": "rate_limit_exceeded"}}), True),
        (_error(429, {"error": {"code": "daily_allowance_exhausted"}}), False),
        (_error(429, {"error": {"type": "insufficient_quota"}}), False),
        (_error(429, {"error": {"code": "billing_hard_limit_reached"}}), False),
        (_error(503, None, **{"x-should-retry": "false"}), False),
        (_error(400), False),
        (ConnectionResetError(), True),
    ],
)
def test_openai_errors_are_retried_only_when_retrying_can_help(
    failure: BaseException, retried: bool
) -> None:
    assert providers.provider_for(REFLECTION, "").is_transient(failure) is retried


def test_errors_are_described_by_status_and_code_only() -> None:
    error = _error(429, {"error": {"code": "daily_allowance_exhausted", "message": "PROMPT"}})
    assert http.describe(error) == "HTTP 429 daily_allowance_exhausted"
    assert http.describe(_error(400, {"error": {"code": "Has spaces and PROMPT"}})) == "HTTP 400"
    assert http.describe(TimeoutError("PROMPT")) == "TimeoutError"


def test_post_json_turns_an_error_status_into_a_model_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> None:
        """Record the request and refuse it."""
        seen.append(request)
        headers = Message()
        headers["x-should-retry"] = "false"
        raise urllib.error.HTTPError(
            request.full_url, 503, "busy", headers, io.BytesIO(b'{"error": {"code": "busy"}}')
        )

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(http.ModelError) as caught:
        http.post_json("https://api.example/x", {"x-api-key": "secret"}, {"a": 1}, 5)
    assert caught.value.status == 503 and caught.value.codes() == ["busy"]
    assert not http.is_retryable_status(caught.value)
    assert seen[0].unredirected_hdrs == {"X-api-key": "secret"}
