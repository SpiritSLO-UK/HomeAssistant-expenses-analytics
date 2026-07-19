"""Unit tests for the OpenAI-compatible provider's transport + JSON handling
(SR-D2).

No real network: ``httpx.Client`` is monkeypatched with a scripted fake and the
retry backoff sleep is stubbed, so tests stay fast and offline.
"""

from __future__ import annotations

import httpx
import pytest

from app.services import ai_provider
from app.services.ai_provider import AIError, OpenAICompatibleProvider, _extract_json


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, *, bad_json: bool = False):
        self.status_code = status_code
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    """Context-manager client whose ``post`` replays a scripted list of
    responses/exceptions (one item consumed per call)."""

    def __init__(self, script: list, calls: list):
        self._script = script
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def post(self, url, headers=None, json=None):
        self._calls.append(url)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok_payload(content="{}"):
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(ai_provider.time, "sleep", lambda s: slept.append(s))
    return slept


def _patch_httpx(monkeypatch, script: list) -> list:
    calls: list = []
    monkeypatch.setattr(httpx, "Client", lambda **_: _FakeClient(script, calls))
    return calls


def _provider():
    return OpenAICompatibleProvider(base_url="http://x/v1", model="m")


# --- _extract_json robustness (greedy span replaced) ---


def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_code_fence():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_with_surrounding_prose():
    text = 'Sure! Here is the result:\n{"category": "Groceries"}\nHope that helps.'
    assert _extract_json(text) == {"category": "Groceries"}


def test_extract_json_first_of_multiple_objects():
    # The old greedy \{.*\} grabbed first-brace..last-brace and failed to parse;
    # we must return the FIRST balanced object.
    text = 'prose {"category": "A"} and then {"category": "B"}'
    assert _extract_json(text) == {"category": "A"}


def test_extract_json_ignores_braces_inside_strings():
    text = 'note {"rationale": "spent at {shop}", "n": 2} trailing'
    assert _extract_json(text) == {"rationale": "spent at {shop}", "n": 2}


def test_extract_json_raises_when_absent():
    with pytest.raises(AIError):
        _extract_json("no json here at all")


# --- HTTP 200 with an error payload -> clear AIError, not KeyError ---


def test_error_payload_on_200_raises_clear_error(monkeypatch, no_sleep):
    _patch_httpx(monkeypatch, [_FakeResponse(200, {"error": {"message": "bad model"}})])
    provider = _provider()
    with pytest.raises(AIError) as exc:
        provider._complete([{"role": "user", "content": "hi"}])
    assert "bad model" in str(exc.value)
    assert "choices" not in str(exc.value)  # no leaked KeyError


def test_missing_choices_raises_clear_error(monkeypatch, no_sleep):
    _patch_httpx(monkeypatch, [_FakeResponse(200, {"unexpected": True})])
    provider = _provider()
    with pytest.raises(AIError) as exc:
        provider._complete([{"role": "user", "content": "hi"}])
    assert "no message content" in str(exc.value)


# --- retry on 429 / 5xx / transient connect ---


def test_retries_5xx_then_succeeds(monkeypatch, no_sleep):
    calls = _patch_httpx(monkeypatch, [
        _FakeResponse(503),
        _FakeResponse(200, _ok_payload('{"ok": 1}')),
    ])
    out = _provider()._complete([{"role": "user", "content": "hi"}])
    assert out == '{"ok": 1}'
    assert len(calls) == 2  # retried once
    assert len(no_sleep) == 1  # backed off once


def test_retries_429_then_succeeds(monkeypatch, no_sleep):
    calls = _patch_httpx(monkeypatch, [
        _FakeResponse(429),
        _FakeResponse(200, _ok_payload("hello")),
    ])
    assert _provider()._complete([{"role": "user", "content": "hi"}]) == "hello"
    assert len(calls) == 2


def test_retries_transient_connect_error(monkeypatch, no_sleep):
    calls = _patch_httpx(monkeypatch, [
        httpx.ConnectError("cold start"),
        _FakeResponse(200, _ok_payload("up")),
    ])
    assert _provider()._complete([{"role": "user", "content": "hi"}]) == "up"
    assert len(calls) == 2


def test_gives_up_after_max_attempts(monkeypatch, no_sleep):
    calls = _patch_httpx(monkeypatch, [_FakeResponse(503) for _ in range(3)])
    provider = _provider()
    with pytest.raises(AIError) as exc:
        provider._complete([{"role": "user", "content": "hi"}])
    assert "after 3 attempts" in str(exc.value)
    assert len(calls) == 3


def test_non_transient_status_not_retried(monkeypatch, no_sleep):
    # A 400 is a client error — surface immediately, don't waste retries.
    calls = _patch_httpx(monkeypatch, [_FakeResponse(400)])
    provider = _provider()
    with pytest.raises(AIError):
        provider._complete([{"role": "user", "content": "hi"}])
    assert len(calls) == 1
    assert no_sleep == []


# --- available() validates the endpoint scheme ---


def test_available_true_for_valid_https():
    assert OpenAICompatibleProvider(base_url="https://api.example.com/v1", model="m").available()


def test_available_true_for_valid_http():
    assert OpenAICompatibleProvider(base_url="http://localhost:11434/v1", model="m").available()


@pytest.mark.parametrize(
    "bad_url",
    [
        "",  # empty
        "api.example.com/v1",  # scheme-less
        "//api.example.com/v1",  # scheme-relative
        "ftp://api.example.com",  # unsupported scheme
        "file:///etc/passwd",  # unsupported scheme
        "http://",  # scheme but no host
        "justtext",  # not a URL at all
    ],
)
def test_available_false_for_malformed_endpoint(bad_url):
    assert not OpenAICompatibleProvider(base_url=bad_url, model="m").available()


def test_available_false_when_model_missing():
    assert not OpenAICompatibleProvider(base_url="https://api.example.com/v1", model="").available()


def test_available_logs_warning_for_bad_scheme(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        assert not OpenAICompatibleProvider(base_url="ftp://x/v1", model="m").available()
    assert any("http(s)" in rec.message or "ftp://x" in str(rec.args) for rec in caplog.records)
