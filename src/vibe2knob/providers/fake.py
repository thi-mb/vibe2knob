"""A scripted provider, for tests and for running the hub without a model or a key."""

from __future__ import annotations

import json
import re
import time
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
    """Plays back scripted replies in order and records each call.

    The last reply repeats, or with `cycle` the replies go round. `delay` pauses between
    words, so a UI can watch the answer stream in.
    """

    name = "fake"
    model = "scripted"

    def __init__(self, replies: list[Reply] | None = None, title: str = "Scripted chat",
                 advice: dict | None = None, cycle: bool = False, delay: float = 0.0):
        self.replies = list(replies or [Reply("Scripted answer.")])
        self.cycle = cycle
        self.delay = delay
        self.title = title
        self.advice = advice
        self.calls: list[dict] = []
        self.title_calls: list[dict] = []

    def stream_chat(
        self, system: str, messages: list[dict], tools: list[ToolSpec]
    ) -> Iterator[StreamEvent]:
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if self.cycle:
            reply = self.replies.pop(0)
            self.replies.append(reply)
        else:
            reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        words = re.findall(r"\s*\S+", reply.text)
        for word in words:
            if self.delay:
                time.sleep(self.delay)
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


def demo_provider(delay: float = 0.03) -> FakeProvider:
    """Canned answers for running the panel with no model and no key (`panel --fake`)."""
    wider = Reply.with_steps(
        "Here's a gentle way to widen it without losing the centre. Start with the stereo "
        "image, then check it in mono.",
        [
            {
                "action": "add_device", "track": None, "device": "Utility", "chain": None,
                "on_track": False, "parameter": "Width", "value_range": "120-140 %",
                "explanation": "Raises the side signal so the sound spreads further out.",
            },
            {
                "action": "adjust", "track": None, "device": "Utility", "chain": None,
                "on_track": False, "parameter": "Bass Mono", "value_range": "On, 120 Hz",
                "explanation": "Keeps the low end in the middle, where it stays punchy.",
            },
        ],
        listen_for="Toggle Utility off and on, and listen to how far the sound reaches.",
        caution="Past about 150 % the sound gets thin in mono.",
    )
    chat = Reply(
        "Good question. This is a scripted answer from the fake model, so there are no "
        "steps this time: a prose-only answer is fine too."
    )
    return FakeProvider([wider, chat], title="Wider without losing mono", cycle=True,
                        delay=delay)
