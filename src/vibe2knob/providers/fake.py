"""A scripted provider, for tests and for running the hub without a model or a key."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterator

from .base import Done, Error, StreamEvent, TextDelta, ToolCall, ToolSpec


@dataclass
class Reply:
    """One scripted answer: prose, plus optional tool calls as (name, input) pairs."""

    text: str = ""
    tool_calls: tuple[tuple[str, dict], ...] = ()
    error: str | None = None

    @classmethod
    def with_steps(cls, text: str, steps: list[dict], listen_for: str = "",
                   caution: str | None = None) -> "Reply":
        payload = {"steps": steps, "listen_for": listen_for, "caution": caution}
        return cls(text, (("suggest_steps", payload),))


class FakeProvider:
    """Plays back scripted replies in order (the last one repeats) and records each call."""

    name = "fake"
    model = "scripted"

    def __init__(self, replies: list[Reply] | None = None, title: str = "Scripted chat",
                 advice: dict | None = None):
        self.replies = list(replies or [Reply("Scripted answer.")])
        self.title = title
        self.advice = advice
        self.calls: list[dict] = []
        self.title_calls: list[dict] = []

    def stream_chat(
        self, system: str, messages: list[dict], tools: list[ToolSpec]
    ) -> Iterator[StreamEvent]:
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        words = re.findall(r"\s*\S+", reply.text)
        for word in words:
            yield TextDelta(word)
        if reply.error:
            yield Error(reply.error)
            return
        for name, payload in reply.tool_calls:
            yield ToolCall(name, payload, f"call_{len(self.calls)}")
        yield Done({"input_tokens": 0, "output_tokens": len(words)}, "end_turn")

    def generate_text(self, system: str, user: str) -> str:
        self.title_calls.append({"system": system, "user": user})
        return self.title

    def generate(self, system: str, user: str, schema: dict) -> str:
        self.calls.append({"system": system, "user": user, "schema": schema})
        return json.dumps(self.advice or {})
