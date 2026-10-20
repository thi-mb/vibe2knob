"""Structured advice returned by the core."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Action = Literal["add_device", "adjust", "enable", "disable", "remove", "reorder"]


class Step(BaseModel):
    action: Action
    device: str = Field(description="Stock Live device name, e.g. 'Utility'.")
    on_track: bool = Field(description="True when the device is already on the track.")
    parameter: str | None = Field(description="Parameter as labelled in Live, or null.")
    value_range: str | None = Field(description="Starting point, e.g. '120-140 %'.")
    explanation: str = Field(description="What it does and why it fits, one or two sentences.")


class Advice(BaseModel):
    interpretation: str = Field(description="What the request means for this track.")
    steps: list[Step]
    listen_for: str = Field(description="What to A/B and listen for after the steps.")
    caution: str | None = Field(description="The main pitfall for this track, or null.")
    warnings: list[str] = Field(
        default_factory=list, description="Added by vibe2knob's checks, not by the model."
    )


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
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action", "device", "on_track", "parameter", "value_range", "explanation",
                ],
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add_device", "adjust", "enable", "disable", "remove", "reorder"],
                    },
                    "device": {"type": "string"},
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


def format_advice(advice: Advice) -> str:
    """Plain-text rendering for the terminal."""
    lines = [advice.interpretation, ""]
    for i, s in enumerate(advice.steps, 1):
        verb = {
            "add_device": "Add",
            "adjust": "On",
            "enable": "Turn on",
            "disable": "Turn off",
            "remove": "Remove",
            "reorder": "Move",
        }[s.action]
        head = f"{i}. {verb} {s.device}"
        if s.parameter:
            head += f" > {s.parameter}"
        if s.value_range:
            head += f": {s.value_range}"
        lines.append(head)
        lines.append(f"   {s.explanation}")
    lines += ["", f"Listen: {advice.listen_for}"]
    if advice.caution:
        lines.append(f"Watch out: {advice.caution}")
    for w in advice.warnings:
        lines.append(f"[check] {w}")
    return "\n".join(lines)
