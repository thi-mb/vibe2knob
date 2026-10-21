"""Claude via the Anthropic API (the default provider)."""

from __future__ import annotations

import os

from .base import ProviderError

DEFAULT_MODEL = "claude-opus-5-5"


class ClaudeProvider:
    name = "claude"

    def __init__(self, model: str | None = None, effort: str = "medium", client=None):
        self.model = model or os.environ.get("VIBE2KNOB_MODEL") or DEFAULT_MODEL
        self.effort = effort
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

    def generate(self, system: str, user: str, schema: dict) -> str:
        import anthropic

        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                # The system prompt holds the whole knowledge map and is identical
                # across requests, so cache it.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                # If a safety classifier declines, retry on a fallback model in the same call.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
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

        if response.stop_reason == "refusal":
            raise ProviderError("Claude declined this request.")
        if response.stop_reason == "max_tokens":
            raise ProviderError("The answer was cut off (max_tokens).")
        text = "".join(b.text for b in response.content if b.type == "text")
        if not text:
            raise ProviderError("Claude returned no text.")
        return text
