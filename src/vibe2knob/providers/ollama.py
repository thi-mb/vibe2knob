"""Local models through Ollama (https://ollama.com), for offline use.

Uses Ollama's structured outputs (`format` = JSON schema). No extra dependencies.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import ProviderError

DEFAULT_MODEL = "llama3.1"


class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str | None = None, host: str | None = None, timeout: float = 300):
        self.model = model or os.environ.get("VIBE2KNOB_OLLAMA_MODEL") or DEFAULT_MODEL
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self.timeout = timeout

    def generate(self, system: str, user: str, schema: dict) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "stream": False,
                "format": schema,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.host}/api/chat", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise ProviderError(f"Could not reach Ollama at {self.host}: {e}") from e
        text = data.get("message", {}).get("content", "")
        if not text:
            raise ProviderError("Ollama returned no text.")
        return text
