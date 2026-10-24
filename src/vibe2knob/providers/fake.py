"""A scripted provider, for tests and for running the hub without a model or a key."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Iterator

from .base import Done, Error, Phase, StreamEvent, TextDelta, ToolCall, ToolSpec


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

    @classmethod
    def reads(cls, *tracks: str, text: str = "", reason: str = "to check it") -> "Reply":
        """A round that reads tracks with read_track; the next reply answers."""
        return cls(text, (("read_track", {"tracks": list(tracks), "reason": reason}),))


class FakeProvider:
    """Plays back scripted replies in order and records each call.

    Each model call takes the next reply, so a turn that reads tracks is scripted as a
    read reply followed by the answer (one reply per round).

    The last reply repeats, or with `cycle` the replies go round. `delay` pauses between
    words, so a UI can watch the answer stream in, and `think` pauses before the first word
    and before the steps, so it can watch the progress line too.
    """

    name = "fake"
    model = "scripted"

    def __init__(self, replies: list[Reply] | None = None, title: str = "Scripted chat",
                 advice: dict | None = None, cycle: bool = False, delay: float = 0.0,
                 think: float = 0.0):
        self.replies = list(replies or [Reply("Scripted answer.")])
        self.cycle = cycle
        self.delay = delay
        self.think = think
        self.title = title
        self.advice = advice
        self.calls: list[dict] = []
        self.title_calls: list[dict] = []
        self.last_usage: dict | None = None

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
        if self.think:
            time.sleep(self.think)
        for word in words:
            if self.delay:
                time.sleep(self.delay)
            yield TextDelta(word)
        if reply.error:
            yield Error(reply.error)
            return
        if reply.tool_calls:
            yield Phase("steps", reply.tool_calls[0][0])
            if self.think:
                time.sleep(self.think)
        for k, (name, payload) in enumerate(reply.tool_calls):
            yield ToolCall(name, payload, f"call_{len(self.calls)}_{k}")
        # Rough counts, about four characters a token, so the usage views have numbers.
        prompt = len(system) + sum(len(m["content"]) + sum(len(r["content"]) for r in
                                   m.get("tool_results", ())) for m in messages)
        yield Done({"input_tokens": prompt // 4, "output_tokens": len(words) * 2,
                    "model": self.model}, "end_turn")

    def generate_text(self, system: str, user: str) -> str:
        self.title_calls.append({"system": system, "user": user})
        self.last_usage = {"input_tokens": (len(system) + len(user)) // 4,
                           "output_tokens": 8, "model": self.model}
        return self.title

    def generate(self, system: str, user: str, schema: dict) -> str:
        self.calls.append({"system": system, "user": user, "schema": schema})
        answer = json.dumps(self.advice or {})
        self.last_usage = {"input_tokens": (len(system) + len(user)) // 4,
                           "output_tokens": len(answer) // 4, "model": self.model}
        return answer


def demo_provider(delay: float = 0.03, think: float = 1.5) -> FakeProvider:
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
                        delay=delay, think=think)
