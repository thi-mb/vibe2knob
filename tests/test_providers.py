from types import SimpleNamespace

import pytest

from vibe2knob.providers import ClaudeProvider, ProviderError, get_provider


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def fake_client(stop_reason="end_turn", text='{"ok": true}'):
    content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
    messages = FakeMessages(SimpleNamespace(stop_reason=stop_reason, content=content))
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_claude_request_shape():
    client, messages = fake_client()
    out = ClaudeProvider(client=client).generate("sys", "user", {"type": "object"})
    assert out == '{"ok": true}'
    kw = messages.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["thinking"] == {"type": "adaptive"}


def test_claude_refusal_raises():
    client, _ = fake_client(stop_reason="refusal", text="")
    with pytest.raises(ProviderError):
        ClaudeProvider(client=client).generate("s", "u", {})


def test_unknown_provider():
    with pytest.raises(ProviderError):
        get_provider("gpt")
