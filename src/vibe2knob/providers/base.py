from __future__ import annotations

from typing import Protocol


class ProviderError(RuntimeError):
    """The model could not produce an answer (missing key, refusal, bad output...)."""


class Provider(Protocol):
    """An LLM backend. It returns JSON text matching `schema`."""

    name: str

    def generate(self, system: str, user: str, schema: dict) -> str: ...
