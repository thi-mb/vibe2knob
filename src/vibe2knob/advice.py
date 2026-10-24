"""Structured advice returned by the core."""

from __future__ import annotations

import copy
from typing import Literal

from pydantic import BaseModel, Field

Action = Literal["add_device", "adjust", "enable", "disable", "remove", "reorder"]


class Step(BaseModel):
    action: Action
    track: str | None = Field(
        default=None,
        description="Track the step is on, as named in the chat's context; null for the only track.",
    )
    device: str = Field(
        description="Exact name as listed on the chain, or the stock device name when adding one."
    )
    chain: str | None = Field(default=None, description="Rack chain the device is in, or null.")
    on_track: bool = Field(description="True when the device is already on the track.")
    parameter: str | None = Field(description="Parameter as labelled in Live, or null.")
    value_range: str | None = Field(description="Starting point, e.g. '120-140 %'.")
    explanation: str = Field(description="What it does and why it fits, one or two sentences.")
    stock_device: str | None = Field(
        default=None, description="Stock device this step uses; filled in by vibe2knob's checks."
    )
    track_index: int | None = Field(
        default=None,
        description="Live's index of the track the step is on; filled in by vibe2knob's checks, "
        "null when the track name is missing or matches more than one track.",
    )


class Advice(BaseModel):
    interpretation: str = Field(description="What the request means for this track.")
    steps: list[Step]
    listen_for: str = Field(description="What to A/B and listen for after the steps.")
    caution: str | None = Field(description="The main pitfall for this track, or null.")
    warnings: list[str] = Field(
        default_factory=list, description="Added by vibe2knob's checks, not by the model."
    )


# The most steps one answer may give. The prompt asks for 2 to 5, and the evals check it.
MAX_STEPS = 5


def _nullable(t: str) -> dict:
    return {"anyOf": [{"type": t}, {"type": "null"}]}


# Written by hand so it stays valid for strict structured outputs
# (every property required, no additional properties).
ADVICE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["interpretation", "steps", "listen_for", "caution"],
    "properties": {
        "interpretation": {"type": "string"},
        "steps": {
            "type": "array",
            "maxItems": MAX_STEPS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action", "device", "chain", "on_track", "parameter", "value_range", "explanation",
                ],
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add_device", "adjust", "enable", "disable", "remove", "reorder"],
                    },
                    "device": {"type": "string"},
                    "chain": _nullable("string"),
                    "on_track": {"type": "boolean"},
                    "parameter": _nullable("string"),
                    "value_range": _nullable("string"),
                    "explanation": {"type": "string"},
                },
            },
        },
        "listen_for": {"type": "string"},
        "caution": _nullable("string"),
    },
}


# The chat path's optional answer tool: the advice fields minus `interpretation` (the prose
# carries that), and each step names its track, since a chat can look at several.
_CHAT_STEP = copy.deepcopy(ADVICE_SCHEMA["properties"]["steps"]["items"])
_CHAT_STEP["properties"] = {"track": _nullable("string"), **_CHAT_STEP["properties"]}
_CHAT_STEP["required"] = ["track", *_CHAT_STEP["required"]]

SUGGEST_STEPS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["steps", "listen_for", "caution"],
    "properties": {
        "steps": {"type": "array", "maxItems": MAX_STEPS, "items": _CHAT_STEP},
        "listen_for": {"type": "string"},
        "caution": _nullable("string"),
    },
}


class SuggestedSteps(BaseModel):
    """The suggest_steps tool input, as validated by the chat engine."""

    steps: list[Step]
    listen_for: str
    caution: str | None = None


def format_step(i: int, s: Step) -> str:
    """One step as a line of text, e.g. '1. On Utility > Stereo Width: 120-140 %'."""
    verb = {
        "add_device": "Add",
        "adjust": "On",
        "enable": "Turn on",
        "disable": "Turn off",
        "remove": "Remove",
        "reorder": "Move",
    }[s.action]
    head = f"{i}. {verb} {s.device}"
    if s.chain:
        head += f" (in the {s.chain} chain)"
    if s.track:
        head += f' on "{s.track}"'
    if s.parameter:
        head += f" > {s.parameter}"
    if s.value_range:
        head += f": {s.value_range}"
    return head


def format_advice(advice: Advice) -> str:
    """Plain-text rendering for the terminal."""
    lines = [advice.interpretation, ""]
    for i, s in enumerate(advice.steps, 1):
        lines.append(format_step(i, s))
        lines.append(f"   {s.explanation}")
    lines += ["", f"Listen: {advice.listen_for}"]
    if advice.caution:
        lines.append(f"Watch out: {advice.caution}")
    for w in advice.warnings:
        lines.append(f"[check] {w}")
    return "\n".join(lines)
