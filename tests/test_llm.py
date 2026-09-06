"""Tests for the optional LLM integration (app/llm.py) - mainly the gating
rule every AI feature depends on: is_enabled() is only true right after a
real test call has succeeded, and saving any config change clears it again."""
import urllib.error

import pytest

from app import llm


class _FakeResponse:
    """Stands in for the object urllib.request.urlopen() returns - a
    context manager whose .read() gives back the response bytes."""

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _fake_urlopen(json_body):
    """Returns a stand-in for urlopen that always succeeds with json_body
    (a bytes string)."""
    def _urlopen(req, timeout=None):
        return _FakeResponse(json_body)
    return _urlopen


def _raising_urlopen(exc):
    def _urlopen(req, timeout=None):
        raise exc
    return _urlopen


def test_default_config(db):
    config = llm.get_config()
    assert config["provider"] == "openai_compatible"
    assert config["base_url"] == ""
    assert config["verified"] is False
    assert config["verified_at"] == ""


def test_is_enabled_false_by_default(db):
    assert llm.is_enabled() is False


def test_save_config_stores_values(db):
    llm.save_config("openai_compatible", "http://localhost:11434/v1/", "secret", "llama3.1:8b")
    config = llm.get_config()
    assert config["provider"] == "openai_compatible"
    # trailing slash is trimmed so "{base_url}/chat/completions" doesn't double up
    assert config["base_url"] == "http://localhost:11434/v1"
    assert config["api_key"] == "secret"
    assert config["model"] == "llama3.1:8b"


def test_save_config_unknown_provider_raises(db):
    with pytest.raises(ValueError):
        llm.save_config("not_a_real_provider", "", "", "")


def test_save_config_resets_verified_flag(db, monkeypatch):
    monkeypatch.setattr(
        llm.urllib.request, "urlopen",
        _fake_urlopen(b'{"choices":[{"message":{"content":"OK"}}]}'),
    )
    llm.save_config("openai_compatible", "http://localhost:11434/v1", "", "llama3.1:8b")
    result = llm.test_connection()
    assert result["ok"] is True
    assert llm.is_enabled() is True

    # Saving again - even with the exact same values - must require a fresh
    # test before anything trusts this connection again.
    llm.save_config("openai_compatible", "http://localhost:11434/v1", "", "llama3.1:8b")
    assert llm.is_enabled() is False


def test_test_connection_success_openai_compatible(db, monkeypatch):
    monkeypatch.setattr(
        llm.urllib.request, "urlopen",
        _fake_urlopen(b'{"choices":[{"message":{"content":"OK"}}]}'),
    )
    llm.save_config("openai_compatible", "http://localhost:11434/v1", "", "llama3.1:8b")
    result = llm.test_connection()
    assert result == {"ok": True, "reply": "OK"}
    assert llm.is_enabled() is True
    assert llm.get_config()["verified_at"] != ""


def test_test_connection_success_anthropic(db, monkeypatch):
    monkeypatch.setattr(
        llm.urllib.request, "urlopen",
        _fake_urlopen(b'{"content":[{"text":"OK"}]}'),
    )
    llm.save_config("anthropic", "", "sk-ant-fake", "claude-sonnet-4-5")
    result = llm.test_connection()
    assert result == {"ok": True, "reply": "OK"}
    assert llm.is_enabled() is True


def test_test_connection_network_failure_marks_unverified(db, monkeypatch):
    monkeypatch.setattr(
        llm.urllib.request, "urlopen",
        _raising_urlopen(urllib.error.URLError("connection refused")),
    )
    llm.save_config("openai_compatible", "http://localhost:11434/v1", "", "llama3.1:8b")
    result = llm.test_connection()
    assert result["ok"] is False
    assert "reach the endpoint" in result["error"]
    assert llm.is_enabled() is False


def test_test_connection_http_error_marks_unverified(db, monkeypatch):
    err = urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None)
    monkeypatch.setattr(err, "read", lambda: b"bad api key")
    monkeypatch.setattr(llm.urllib.request, "urlopen", _raising_urlopen(err))
    llm.save_config("anthropic", "", "wrong-key", "claude-sonnet-4-5")
    result = llm.test_connection()
    assert result["ok"] is False
    assert "401" in result["error"]
    assert llm.is_enabled() is False


def test_test_connection_missing_base_url(db):
    llm.save_config("openai_compatible", "", "", "llama3.1:8b")
    result = llm.test_connection()
    assert result["ok"] is False
    assert "base URL" in result["error"]


def test_test_connection_missing_anthropic_key(db):
    llm.save_config("anthropic", "", "", "claude-sonnet-4-5")
    result = llm.test_connection()
    assert result["ok"] is False
    assert "API key" in result["error"]


def test_generate_raises_when_not_enabled(db):
    llm.save_config("openai_compatible", "http://localhost:11434/v1", "", "llama3.1:8b")
    with pytest.raises(llm.LLMError):
        llm.generate("system prompt", "user prompt")


def test_generate_succeeds_after_verified(db, monkeypatch):
    monkeypatch.setattr(
        llm.urllib.request, "urlopen",
        _fake_urlopen(b'{"choices":[{"message":{"content":"A recap of last time."}}]}'),
    )
    llm.save_config("openai_compatible", "http://localhost:11434/v1", "", "llama3.1:8b")
    llm.test_connection()
    assert llm.is_enabled() is True

    reply = llm.generate("You are a GM assistant.", "Summarize: the party found a door.")
    assert reply == "A recap of last time."


def test_openai_compatible_sends_bearer_header_only_when_key_set(db, monkeypatch):
    captured = {}

    def _urlopen(req, timeout=None):
        captured["headers"] = dict(req.headers)
        return _FakeResponse(b'{"choices":[{"message":{"content":"OK"}}]}')

    monkeypatch.setattr(llm.urllib.request, "urlopen", _urlopen)
    llm.save_config("openai_compatible", "http://localhost:11434/v1", "", "llama3.1:8b")
    llm.test_connection()
    assert "Authorization" not in captured["headers"]

    llm.save_config("openai_compatible", "http://localhost:11434/v1", "sk-abc", "llama3.1:8b")
    llm.test_connection()
    assert captured["headers"]["Authorization"] == "Bearer sk-abc"


def test_anthropic_sends_x_api_key_header(db, monkeypatch):
    captured = {}

    def _urlopen(req, timeout=None):
        captured["headers"] = dict(req.headers)
        captured["url"] = req.full_url
        return _FakeResponse(b'{"content":[{"text":"OK"}]}')

    monkeypatch.setattr(llm.urllib.request, "urlopen", _urlopen)
    llm.save_config("anthropic", "", "sk-ant-fake", "claude-sonnet-4-5")
    llm.test_connection()
    assert captured["headers"]["X-api-key"] == "sk-ant-fake"
    assert captured["url"] == llm._ANTHROPIC_MESSAGES_URL


def test_malformed_response_raises_llm_error_and_unverifies(db, monkeypatch):
    monkeypatch.setattr(
        llm.urllib.request, "urlopen",
        _fake_urlopen(b'{"unexpected": "shape"}'),
    )
    llm.save_config("openai_compatible", "http://localhost:11434/v1", "", "llama3.1:8b")
    result = llm.test_connection()
    assert result["ok"] is False
    assert llm.is_enabled() is False


def test_non_json_response_raises_llm_error(db, monkeypatch):
    monkeypatch.setattr(
        llm.urllib.request, "urlopen",
        _fake_urlopen(b"not json at all"),
    )
    llm.save_config("openai_compatible", "http://localhost:11434/v1", "", "llama3.1:8b")
    result = llm.test_connection()
    assert result["ok"] is False
    assert "JSON" in result["error"]
