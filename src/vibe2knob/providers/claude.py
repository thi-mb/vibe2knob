"""Claude via the Anthropic API (the default provider)."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from typing import Iterator

from .. import pricing
from .base import Done, Error, Phase, ProviderError, StreamEvent, TextDelta, ToolCall, ToolSpec

DEFAULT_MODEL = "claude-opus-5-5"
TITLE_MODEL = "claude-haiku-5-5"

# If a safety classifier declines, retry on a fallback model in the same call.
_FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


class ClaudeProvider:
    name = "claude"

    def __init__(self, model: str | None = None, effort: str = "medium", client=None,
                 title_model: str = TITLE_MODEL, api_key: str | None = None):
        self.model = model or os.environ.get("VIBE2KNOB_MODEL") or DEFAULT_MODEL
        self.effort = effort
        self.title_model = title_model
        self._client = client
        # None lets the SDK find credentials itself (ANTHROPIC_API_KEY, `ant auth login`).
        self.api_key = api_key
        self.last_usage: dict | None = None
        self._counts: dict[str, dict | None] = {}
        self._window: int | None = None

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover - depends on install
                raise ProviderError(
                    "The Claude provider needs the anthropic package: pip install 'vibe2knob[claude]'"
                ) from e
            self._client = (anthropic.Anthropic(api_key=self.api_key) if self.api_key
                            else anthropic.Anthropic())
        return self._client

    def _system(self, system: str) -> list[dict]:
        # The system prompt holds the whole knowledge map and is identical
        # across requests, so cache it.
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    def generate(self, system: str, user: str, schema: dict) -> str:
        with _api_errors():
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self._system(system),
                messages=[{"role": "user", "content": user}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                **_FALLBACK,
            )
        self.last_usage = _usage(response)
        _check_stop(response.stop_reason)
        text = "".join(b.text for b in response.content if b.type == "text")
        if not text:
            raise ProviderError("Claude returned no text.")
        return text

    def stream_chat(
        self, system: str, messages: list[dict], tools: list[ToolSpec]
    ) -> Iterator[StreamEvent]:
        # Tools render before the system prompt, so keep their order stable for caching.
        # Step inputs are small, so they stream buffered (no eager_input_streaming), which
        # keeps the server's strict schema validation.
        tool_defs = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema,
             "strict": t.strict}
            for t in tools
        ]
        try:
            with _api_errors():
                with self.client.beta.messages.stream(
                    model=self.model,
                    max_tokens=16000,
                    system=self._system(system),
                    messages=_messages(messages),
                    tools=tool_defs,
                    thinking={"type": "adaptive"},
                    output_config={"effort": self.effort},
                    **_FALLBACK,
                ) as stream:
                    for event in stream:
                        if event.type == "content_block_delta" and event.delta.type == "text_delta":
                            yield TextDelta(event.delta.text)
                        elif (event.type == "content_block_start"
                              and event.content_block.type == "tool_use"):
                            yield Phase("steps", getattr(event.content_block, "name", None))
                    final = stream.get_final_message()
            # A refusal or a cut-off can leave a half-written tool call: don't use it.
            _check_stop(final.stop_reason)
        except ProviderError as e:
            yield Error(str(e))
            return
        for block in final.content:
            if block.type == "tool_use":
                yield ToolCall(block.name, dict(block.input), block.id)
        yield Done(_usage(final), final.stop_reason, raw=final.content)

    def generate_text(self, system: str, user: str) -> str:
        with _api_errors():
            response = self.client.messages.create(
                model=self.title_model,
                max_tokens=2000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low"},
            )
        self.last_usage = _usage(response)
        _check_stop(response.stop_reason)
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def count_parts(self, system: str, tools: list[ToolSpec]) -> dict | None:
        """Exact tokens of the instructions and the tool definitions, counted once per prompt.

        count_tokens is free (but rate-limited). None if it can't be reached.
        """
        tool_defs = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                     for t in tools]
        key = hashlib.sha256(json.dumps([self.model, system, tool_defs]).encode()).hexdigest()
        if key not in self._counts:
            probe = [{"role": "user", "content": "."}]
            try:
                count = self.client.messages.count_tokens
                bare = count(model=self.model, messages=probe).input_tokens
                with_system = count(model=self.model, system=system, messages=probe).input_tokens
                with_tools = count(model=self.model, system=system, messages=probe,
                                   tools=tool_defs).input_tokens
                self._counts[key] = {"instructions": max(0, with_system - bare),
                                     "tools": max(0, with_tools - with_system)}
            except Exception:  # noqa: BLE001 - without it the split is estimated instead
                return None
        return self._counts[key]

    def context_window(self) -> int | None:
        """The model's context window from the Models API, or the bundled table."""
        if self._window is None:
            try:
                self._window = self.client.models.retrieve(self.model).max_input_tokens
            except Exception:  # noqa: BLE001
                return pricing.context_window(self.model)
        return self._window


def _messages(messages: list[dict]) -> list[dict]:
    """The engine's messages as Messages API params (see providers/base.py for the shapes).

    The newest message carries the second cache breakpoint, so the next read round of the
    same turn reads everything before it from cache.
    """
    out = []
    for m in messages:
        if m.get("tool_results"):
            content: list | str = [
                {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]}
                for r in m["tool_results"]
            ]
        elif m["role"] == "assistant" and m.get("raw") is not None:
            # Passed back as it came, so its thinking blocks stay valid.
            content = m["raw"]
        elif m["role"] == "assistant" and m.get("tool_calls"):
            content = ([{"type": "text", "text": m["content"]}] if m["content"] else []) + [
                {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]}
                for c in m["tool_calls"]
            ]
        else:
            content = m["content"]
        out.append({"role": m["role"], "content": content})
    if out and out[-1]["role"] == "user":
        last = out[-1]
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"]}]
        last["content"][-1] = {**last["content"][-1], "cache_control": {"type": "ephemeral"}}
    return out


def _check_stop(stop_reason: str | None) -> None:
    if stop_reason == "refusal":
        raise ProviderError("Claude declined this request.")
    if stop_reason == "max_tokens":
        raise ProviderError("The answer was cut off (max_tokens).")


def _usage(message) -> dict:
    """The usage of a response, with the model that actually answered (a fallback may)."""
    usage = getattr(message, "usage", None)
    if usage is None:
        return {}
    keys = ["input_tokens", "output_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens"]
    out = {k: getattr(usage, k, None) or 0 for k in keys}
    details = getattr(usage, "output_tokens_details", None)
    out["thinking_tokens"] = getattr(details, "thinking_tokens", None) or 0
    model = getattr(message, "model", None)
    if isinstance(model, str):
        out["model"] = model
    return out


@contextmanager
def _api_errors():
    import anthropic

    try:
        yield
    except TypeError as e:
        # The SDK raises TypeError before sending when it finds no credentials.
        if "authentication" not in str(e):
            raise
        raise ProviderError(
            "No Anthropic credentials found. Set ANTHROPIC_API_KEY or run `ant auth login`, "
            "or try --provider ollama for a local model."
        ) from e
    except anthropic.AuthenticationError as e:
        raise ProviderError(
            "Claude rejected the credentials. Set ANTHROPIC_API_KEY or run `ant auth login`."
        ) from e
    except anthropic.APIConnectionError as e:
        raise ProviderError(f"Could not reach the Anthropic API: {e}") from e
    except anthropic.APIStatusError as e:
        raise ProviderError(f"Anthropic API error {e.status_code}: {e.message}") from e
