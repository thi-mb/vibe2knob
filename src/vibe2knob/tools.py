"""Tools the chat model may call.

v0.1 has one: `suggest_steps`, the optional structured part of an answer. Later tools plug
in here: `read_track` (v0.2, a `read` tool whose result goes back to the model) and actions
that change Live (`action` tools, allowed only in Do mode and always confirmed by the user).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from pydantic import ValidationError

from .advice import SUGGEST_STEPS_SCHEMA, SuggestedSteps
from .core import check_steps
from .providers.base import ToolSpec
from .state import TrackState

ToolKind = Literal["answer", "read", "action"]


@dataclass
class ToolContext:
    """What a tool can see while handling a call: every track in the chat's context."""

    tracks: list[TrackState]
    default_track: TrackState | None = None


@dataclass
class ToolOutcome:
    """What handling a call produced. `steps` is set by suggest_steps."""

    steps: SuggestedSteps | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class Tool:
    spec: ToolSpec
    handle: Callable[[dict, ToolContext], ToolOutcome]
    # "answer" tools end the turn; "read" and "action" tools (later) return a result to
    # the model, and "action" tools are gated by Teach/Do mode.
    kind: ToolKind = "answer"


class ToolRegistry:
    def __init__(self, tools: list[Tool] = ()):
        self._tools: dict[str, Tool] = {}
        for t in tools:
            self.register(t)

    def register(self, tool: Tool) -> None:
        self._tools[tool.spec.name] = tool

    def specs(self) -> list[ToolSpec]:
        # Registration order, so the request prefix stays byte-stable for caching.
        return [t.spec for t in self._tools.values()]

    def handle(self, name: str, payload: dict, ctx: ToolContext) -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome(warnings=[f"The model called an unknown tool '{name}'."])
        return tool.handle(payload, ctx)


def _suggest_steps(payload: dict, ctx: ToolContext) -> ToolOutcome:
    try:
        suggested = SuggestedSteps.model_validate(payload)
    except ValidationError as e:
        return ToolOutcome(
            warnings=[f"The suggested steps came back malformed ({e.error_count()} errors)."]
        )
    warnings = check_steps(suggested.steps, ctx.tracks, ctx.default_track)
    return ToolOutcome(steps=suggested, warnings=warnings)


SUGGEST_STEPS = Tool(
    ToolSpec(
        name="suggest_steps",
        description=(
            "Show the user concrete steps with Live's stock devices, as cards they can follow. "
            "Optional: call it once, after your prose, only when the user wants a sound changed "
            "and a track in the context fits. Don't call it for questions or explanations."
        ),
        input_schema=SUGGEST_STEPS_SCHEMA,
    ),
    _suggest_steps,
)


def default_tools() -> ToolRegistry:
    return ToolRegistry([SUGGEST_STEPS])
