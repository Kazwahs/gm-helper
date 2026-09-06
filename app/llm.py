"""Optional LLM integration: a small, provider-agnostic client plus the
config/gating logic that decides whether any AI-powered feature is allowed
to show up in the UI at all.

Deliberately hybrid: GM Helper can talk to either an OpenAI-compatible
chat-completions endpoint (this covers Ollama, LM Studio, OpenAI itself,
and most other local/hosted servers - they all speak the same wire format)
or Anthropic's native Messages API, picked by one "provider" setting. Only
one small module needs to change if a new provider format shows up later.

The gating rule the Settings page and every AI feature depends on:
`is_enabled()` is true only right after a real test call to the configured
endpoint has succeeded. Saving a new/changed config immediately clears that
flag - a feature never trusts a configuration that hasn't just been proven
to work, and pointing the app at a different endpoint always requires a
fresh test before anything AI-powered reappears.
"""
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import database

PROVIDERS = {
    "openai_compatible": "Local / OpenAI-compatible (Ollama, LM Studio, OpenAI, ...)",
    "anthropic": "Anthropic Claude API",
}
DEFAULT_PROVIDER = "openai_compatible"

_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"

_CONFIG_KEYS = ("ai_provider", "ai_base_url", "ai_api_key", "ai_model")


class LLMError(Exception):
    """Anything that goes wrong calling the configured endpoint - a bad
    config, a network failure, or an error response from the server."""


def get_config():
    return {
        "provider": database.get_setting("ai_provider", DEFAULT_PROVIDER) or DEFAULT_PROVIDER,
        "base_url": database.get_setting("ai_base_url", ""),
        "api_key": database.get_setting("ai_api_key", ""),
        "model": database.get_setting("ai_model", ""),
        "verified": database.get_setting("ai_verified", "") == "1",
        "verified_at": database.get_setting("ai_verified_at", ""),
    }


def save_config(provider, base_url, api_key, model):
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    database.set_setting("ai_provider", provider)
    database.set_setting("ai_base_url", (base_url or "").strip().rstrip("/"))
    database.set_setting("ai_api_key", (api_key or "").strip())
    database.set_setting("ai_model", (model or "").strip())
    # Any change to the config it was tested against invalidates that test -
    # a feature should never trust a connection that hasn't just been proven
    # to work against the *current* settings.
    _mark_unverified()


def is_enabled():
    """The one gate every AI-powered feature and every bit of AI UI checks
    before showing itself."""
    return database.get_setting("ai_verified", "") == "1"


def _mark_verified():
    database.set_setting("ai_verified", "1")
    database.set_setting("ai_verified_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))


def _mark_unverified():
    database.set_setting("ai_verified", "")
    database.set_setting("ai_verified_at", "")


def _request(url, headers, payload, timeout):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise LLMError(f"Server responded with HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise LLMError(f"Couldn't reach the endpoint: {e.reason}")
    except TimeoutError:
        raise LLMError("Timed out waiting for a response.")
    except (json.JSONDecodeError, ValueError):
        raise LLMError("Endpoint responded, but not with valid JSON. Is the URL correct?")


def _call_openai_compatible(config, system_prompt, user_prompt, max_tokens, timeout):
    base_url = config["base_url"]
    if not base_url:
        raise LLMError("Set a base URL for the local/OpenAI-compatible endpoint first.")
    if not config["model"]:
        raise LLMError("Set a model name first.")
    headers = {"Content-Type": "application/json"}
    if config["api_key"]:
        headers["Authorization"] = f"Bearer {config['api_key']}"
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    payload = {"model": config["model"], "messages": messages, "max_tokens": max_tokens}
    data = _request(f"{base_url}/chat/completions", headers, payload, timeout)
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"Unexpected response shape from the endpoint: {data!r}"[:300])


def _call_anthropic(config, system_prompt, user_prompt, max_tokens, timeout):
    if not config["api_key"]:
        raise LLMError("Set an Anthropic API key first.")
    if not config["model"]:
        raise LLMError("Set a model name first (e.g. claude-sonnet-4-5).")
    headers = {
        "Content-Type": "application/json",
        "x-api-key": config["api_key"],
        "anthropic-version": _ANTHROPIC_VERSION,
    }
    payload = {
        "model": config["model"],
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if system_prompt:
        payload["system"] = system_prompt
    data = _request(_ANTHROPIC_MESSAGES_URL, headers, payload, timeout)
    try:
        return data["content"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"Unexpected response shape from the endpoint: {data!r}"[:300])


def _dispatch(config, system_prompt, user_prompt, max_tokens, timeout):
    if config["provider"] == "anthropic":
        return _call_anthropic(config, system_prompt, user_prompt, max_tokens, timeout)
    return _call_openai_compatible(config, system_prompt, user_prompt, max_tokens, timeout)


def test_connection():
    """Sends one small real request to the configured endpoint. On success,
    marks the config verified (this is what flips is_enabled() on) and
    returns the model's reply so the Settings page can show real proof it
    worked, not just a checkmark. On failure, clears the verified flag and
    returns the error."""
    config = get_config()
    try:
        reply = _dispatch(
            config,
            system_prompt="You are a connection test for a self-hosted app. Reply with only the word OK.",
            user_prompt="Reply with only the word OK.",
            max_tokens=10,
            timeout=30,
        )
    except LLMError as e:
        _mark_unverified()
        return {"ok": False, "error": str(e)}
    _mark_verified()
    return {"ok": True, "reply": reply}


def generate(system_prompt, user_prompt, max_tokens=700):
    """For real feature use, once a connection has been verified. Callers
    (routes) should still gate on is_enabled() before even offering the
    feature - this is a defense-in-depth check, not the primary gate."""
    if not is_enabled():
        raise LLMError("AI features aren't enabled yet - test a connection in Settings first.")
    config = get_config()
    return _dispatch(config, system_prompt, user_prompt, max_tokens, timeout=120)
