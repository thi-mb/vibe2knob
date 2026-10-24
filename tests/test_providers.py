import io
import json
import urllib.error
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


# stream_chat


from vibe2knob.providers import OllamaProvider  # noqa: E402
from vibe2knob.providers.base import Done, Error, Phase, TextDelta, ToolCall, ToolSpec  # noqa: E402

TOOLS = [ToolSpec("suggest_steps", "Show steps.", {"type": "object"})]


class FakeStream:
    def __init__(self, events, final):
        self.events, self.final = events, final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self):
        return self.final


def stream_client(stop_reason="tool_use"):
    def delta(text):
        return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=text))

    def start(kind):
        block = SimpleNamespace(type=kind, name="suggest_steps") if kind == "tool_use" else SimpleNamespace(type=kind)
        return SimpleNamespace(type="content_block_start", content_block=block)

    final = SimpleNamespace(
        stop_reason=stop_reason,
        content=[
            SimpleNamespace(type="text", text="Open it up."),
            SimpleNamespace(type="tool_use", name="suggest_steps", id="toolu_1", input={"steps": []}),
        ],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=900,
                              cache_creation_input_tokens=0),
    )
    calls = {}

    def stream(**kwargs):
        calls.update(kwargs)
        return FakeStream([start("thinking"), start("text"), delta("Open "), delta("it up."),
                           start("tool_use")], final)

    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream))), calls


def test_claude_stream_chat():
    client, kw = stream_client()
    events = list(ClaudeProvider(client=client).stream_chat("sys", [{"role": "user", "content": "hi"}], TOOLS))
    # Only a tool call's start becomes a phase: the hub already says "thinking" until text.
    assert events[:3] == [TextDelta("Open "), TextDelta("it up."), Phase("steps", "suggest_steps")]
    assert events[3] == ToolCall("suggest_steps", {"steps": []}, "toolu_1")
    assert isinstance(events[4], Done) and events[4].usage["cache_read_input_tokens"] == 900
    assert kw["tools"] == [{"name": "suggest_steps", "description": "Show steps.",
                            "input_schema": {"type": "object"}, "strict": True}]
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["fallbacks"] == "default"


def test_claude_stream_cut_off_drops_tool_call():
    client, _ = stream_client(stop_reason="max_tokens")
    events = list(ClaudeProvider(client=client).stream_chat("s", [], TOOLS))
    assert isinstance(events[-1], Error) and not any(isinstance(e, ToolCall) for e in events)


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.body.splitlines(keepends=True))

    def read(self):
        return self.body


def test_ollama_stream_chat(monkeypatch):
    lines = [
        {"message": {"role": "assistant", "content": "Open "}, "done": False},
        {"message": {"role": "assistant", "content": "it up."}, "done": False},
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "suggest_steps", "arguments": {"steps": []}}}]}, "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": True,
         "prompt_eval_count": 12, "eval_count": 7},
    ]
    sent = []
    provider = OllamaProvider()

    def post(body):
        sent.append(body)
        return FakeResponse("\n".join(json.dumps(x) for x in lines).encode())

    monkeypatch.setattr(provider, "_post", post)
    events = list(provider.stream_chat("sys", [{"role": "user", "content": "hi"}], TOOLS))
    assert events == [TextDelta("Open "), TextDelta("it up."), ToolCall("suggest_steps", {"steps": []}, "call_0"),
                      Done({"input_tokens": 12, "output_tokens": 7, "model": "llama3.1"}, "end_turn")]
    assert sent[0]["messages"][0] == {"role": "system", "content": "sys"}
    assert sent[0]["tools"][0]["function"]["name"] == "suggest_steps"


def test_ollama_falls_back_to_json_without_tools(monkeypatch):
    provider = OllamaProvider()
    sent = []

    def post(body):
        sent.append(body)
        if body["stream"]:
            raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error":"llama2 does not support tools"}'))
        answer = {"answer": "Open it up.", "suggest_steps": {"steps": [], "listen_for": "x", "caution": None}}
        return FakeResponse(json.dumps({"message": {"content": json.dumps(answer)}}).encode())

    monkeypatch.setattr(provider, "_post", post)
    events = list(provider.stream_chat("sys", [], TOOLS))
    assert events[0] == TextDelta("Open it up.")
    assert events[1].name == "suggest_steps" and isinstance(events[2], Done)
    assert sent[1]["format"]["required"] == ["answer", "suggest_steps"]


def test_claude_usage_names_the_model_and_thinking():
    from vibe2knob.providers.claude import _usage

    message = SimpleNamespace(model="claude-opus-5", usage=SimpleNamespace(
        input_tokens=3, output_tokens=40, cache_read_input_tokens=None,
        cache_creation_input_tokens=7, output_tokens_details=SimpleNamespace(thinking_tokens=30)))
    assert _usage(message) == {"input_tokens": 3, "output_tokens": 40, "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 7, "thinking_tokens": 30,
                               "model": "claude-opus-5"}


def test_claude_count_parts_is_counted_once():
    calls = []

    def count_tokens(**kw):
        calls.append(kw)
        n = 5 + (100 if "system" in kw else 0) + (20 if "tools" in kw else 0)
        return SimpleNamespace(input_tokens=n)

    client = SimpleNamespace(messages=SimpleNamespace(count_tokens=count_tokens))
    provider = ClaudeProvider(client=client)
    assert provider.count_parts("sys", TOOLS) == {"instructions": 100, "tools": 20}
    assert provider.count_parts("sys", TOOLS) == {"instructions": 100, "tools": 20}
    assert len(calls) == 3


def test_claude_count_parts_failure_is_none():
    def boom(**kw):
        raise RuntimeError("offline")

    provider = ClaudeProvider(client=SimpleNamespace(messages=SimpleNamespace(count_tokens=boom)))
    assert provider.count_parts("sys", TOOLS) is None
