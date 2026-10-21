"""Pluggable LLM backends. Add one by implementing `Provider` and registering it here."""

from __future__ import annotations

from .base import Provider, ProviderError
from .claude import ClaudeProvider
from .ollama import OllamaProvider

PROVIDERS = {
    "claude": ClaudeProvider,
    "ollama": OllamaProvider,
}


def get_provider(name: str = "claude", model: str | None = None) -> Provider:
    try:
        cls = PROVIDERS[name]
    except KeyError:
        raise ProviderError(
            f"Unknown provider {name!r}. Choose one of: {', '.join(PROVIDERS)}"
        ) from None
    return cls(model=model)


__all__ = ["Provider", "ProviderError", "PROVIDERS", "get_provider", "ClaudeProvider", "OllamaProvider"]
