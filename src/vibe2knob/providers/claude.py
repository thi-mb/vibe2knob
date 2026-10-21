"""Claude via the Anthropic API (the default provider)."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from .base import Done, Error, ProviderError, StreamEvent, TextDelta, ToolCall, ToolSpec

DEFAULT_MODEL = "claude-opus-5-5"
TITLE_MODEL = "claude-haiku-5-5"

# If a safety classifier declines, retry on a fallback model in the same call.
_FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


class ClaudeProvider:
    name = "claude"

    def __init__(self, model: str | None = None, effort: str = "medium", client=None,
                 title_model: str = TITLE_MODEL):
        self.model = model or os.environ.get("VIBE2KNOB_MODEL") or DEFAULT_MODEL
        self.effort = effort
        self.title_model = title_model
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover - depends on install
                raise ProviderError(
                    "The Claude provider needs the anthropic package: pip install 'vibe2knob[claude]'"
                ) from e
            self._client = anthropic.Anthropic()
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
                    messages=messages,
                    tools=tool_defs,
                    thinking={"type": "adaptive"},
                    output_config={"effort": self.effort},
                    **_FALLBACK,
                ) as stream:
                    for event in stream:
                        if event.type == "content_block_delta" and event.delta.type == "text_delta":
                            yield TextDelta(event.delta.text)
                    final = stream.get_final_message()
            # A refusal or a cut-off can leave a half-written tool call: don't use it.
            _check_stop(final.stop_reason)
        except ProviderError as e:
            yield Error(str(e))
            return
        for block in final.content:
            if block.type == "tool_use":
                yield ToolCall(block.name, dict(block.input), block.id)
        yield Done(_usage(final.usage), final.stop_reason)

    def generate_text(self, system: str, user: str) -> str:
        with _api_errors():
            response = self.client.messages.create(
                model=self.title_model,
                max_tokens=2000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low"},
            )
        _check_stop(response.stop_reason)
        return "".join(b.text for b in response.content if b.type == "text").strip()


def _check_stop(stop_reason: str | None) -> None:
    if stop_reason == "refusal":
        raise ProviderError("Claude declined this request.")
    if stop_reason == "max_tokens":
        raise ProviderError("The answer was cut off (max_tokens).")


def _usage(usage) -> dict:
    keys = ["input_tokens", "output_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens"]
    return {k: getattr(usage, k, None) or 0 for k in keys}


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
