"""Local models through Ollama (https://ollama.com), for offline use.

Uses Ollama's structured outputs (`format` = JSON schema) and its streamed /api/chat
with tools. No extra dependencies.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Iterator

from .base import Done, Error, ProviderError, StreamEvent, TextDelta, ToolCall, ToolSpec

DEFAULT_MODEL = "llama3.1"


class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str | None = None, host: str | None = None, timeout: float = 300):
        self.model = model or os.environ.get("VIBE2KNOB_OLLAMA_MODEL") or DEFAULT_MODEL
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self.timeout = timeout
        self.last_usage: dict | None = None

    def _post(self, body: dict):
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps({"model": self.model, **body}).encode(),
            headers={"Content-Type": "application/json"},
        )
        return urllib.request.urlopen(req, timeout=self.timeout)

    def _chat(self, messages: list[dict], fmt: dict | None = None) -> str:
        body: dict = {"stream": False, "messages": messages}
        if fmt is not None:
            body["format"] = fmt
        try:
            with self._post(body) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise ProviderError(f"Could not reach Ollama at {self.host}: {e}") from e
        self.last_usage = {"input_tokens": data.get("prompt_eval_count", 0),
                           "output_tokens": data.get("eval_count", 0), "model": self.model}
        text = data.get("message", {}).get("content", "")
        if not text:
            raise ProviderError("Ollama returned no text.")
        return text

    def generate(self, system: str, user: str, schema: dict) -> str:
        return self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}], schema
        )

    def generate_text(self, system: str, user: str) -> str:
        return self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        ).strip()

    def stream_chat(
        self, system: str, messages: list[dict], tools: list[ToolSpec]
    ) -> Iterator[StreamEvent]:
        msgs = [{"role": "system", "content": system}, *messages]
        body = {
            "stream": True,
            "messages": msgs,
            "tools": [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description, "parameters": t.input_schema,
                }}
                for t in tools
            ],
        }
        try:
            resp = self._post(body)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if tools and "tool" in detail.lower():
                # This model can't call tools: ask for one JSON answer instead.
                yield from self._json_fallback(msgs, tools)
                return
            yield Error(f"Ollama error {e.code}: {detail.strip()}")
            return
        except urllib.error.URLError as e:
            yield Error(f"Could not reach Ollama at {self.host}: {e}")
            return

        usage: dict = {}
        with resp:
            for line in resp:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    yield Error(f"Ollama error: {chunk['error']}")
                    return
                message = chunk.get("message", {})
                if message.get("content"):
                    yield TextDelta(message["content"])
                for call in message.get("tool_calls") or []:
                    fn = call.get("function", {})
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            yield Error(f"Ollama returned unreadable arguments for {fn.get('name')}.")
                            return
                    yield ToolCall(fn.get("name", ""), args)
                if chunk.get("done"):
                    usage = {
                        "input_tokens": chunk.get("prompt_eval_count", 0),
                        "output_tokens": chunk.get("eval_count", 0),
                        "model": self.model,
                    }
        yield Done(usage, "end_turn")

    def _json_fallback(self, msgs: list[dict], tools: list[ToolSpec]) -> Iterator[StreamEvent]:
        """One structured (non-streamed) answer: prose plus an optional input per tool."""
        props: dict = {"answer": {"type": "string"}}
        for t in tools:
            props[t.name] = {"anyOf": [t.input_schema, {"type": "null"}]}
        schema = {"type": "object", "additionalProperties": False,
                  "required": list(props), "properties": props}
        note = {
            "role": "system",
            "content": "Reply with JSON: `answer` holds your prose, and each tool's field holds "
            "the input you would call it with, or null to not call it.",
        }
        try:
            data = json.loads(self._chat([*msgs, note], schema))
        except (ProviderError, json.JSONDecodeError) as e:
            yield Error(str(e))
            return
        if data.get("answer"):
            yield TextDelta(data["answer"])
        for t in tools:
            if isinstance(data.get(t.name), dict):
                yield ToolCall(t.name, data[t.name])
        yield Done(dict(self.last_usage or {}), "end_turn")
