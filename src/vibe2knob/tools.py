"""Tools the chat model may call.

- `suggest_steps` ("answer"): the optional structured part of an answer.
- `read_track` ("read"): the full detail of tracks the model only knows from the summary.
  Its result goes back to the model, which answers in the next round (see ChatEngine.send).

Later, actions that change Live plug in here too (`action` tools, allowed only in Do mode and
always confirmed by the user).
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from typing import Callable, Literal

from pydantic import ValidationError

from .advice import SUGGEST_STEPS_SCHEMA, SuggestedSteps
from .core import check_steps
from .providers.base import ToolSpec
from .session import LiveView, SessionSummary, as_track_state
from .state import TrackState

log = logging.getLogger("vibe2knob.tools")

ToolKind = Literal["answer", "read", "action"]


@dataclass
class Look:
    """A track a read tool read: its index, full state, and why the model wanted it."""

    index: int
    state: TrackState
    why: str = ""


@dataclass
class ToolContext:
    """What a tool can see while handling a call: every track in the chat's context, and for
    read tools, Live itself.

    `detail` holds the indexes that already have full detail this turn. A read tool appends
    what it read to `looked` and spends `reads_left`.
    """

    tracks: list[TrackState]
    default_track: TrackState | None = None
    live: LiveView | None = None
    summary: SessionSummary | None = None
    detail: set[int] = field(default_factory=set)
    looked: list[Look] = field(default_factory=list)
    reads_left: int = 0


@dataclass
class ToolOutcome:
    """What handling a call produced. `steps` is set by suggest_steps; `result` is the text a
    read tool sends back to the model."""

    steps: SuggestedSteps | None = None
    warnings: list[str] = field(default_factory=list)
    result: str | None = None


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

    def kind(self, name: str) -> ToolKind | None:
        tool = self._tools.get(name)
        return tool.kind if tool else None

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


MAX_TRACKS_PER_READ = 3
BUDGET_USED = "Read budget used for this answer: answer with what you have."


def resolve_track(name: str, summary: SessionSummary) -> tuple[int | None, list[str]]:
    """A track name as the model wrote it -> (index, []), or (None, candidates).

    Exact first, then ignoring case, then a unique prefix. Ambiguous or unknown names give
    the names they might have meant.
    """
    want = name.strip().strip('"')
    names = [(t.index, t.name) for t in summary.tracks]
    for match in (lambda n: n == want, lambda n: n.casefold() == want.casefold(),
                  lambda n: n.casefold().startswith(want.casefold())):
        hits = [(i, n) for i, n in names if match(n)]
        if len(hits) == 1:
            return hits[0][0], []
        if len(hits) > 1:
            return None, [n for _, n in hits]
    lowered = {n.casefold(): n for _, n in names}
    close = difflib.get_close_matches(want.casefold(), list(lowered), n=5, cutoff=0.5)
    near = [n for _, n in names if want.casefold() in n.casefold()]
    return None, list(dict.fromkeys(near + [lowered[c] for c in close]))[:5]


def _read_track(payload: dict, ctx: ToolContext) -> ToolOutcome:
    wanted = [t for t in payload.get("tracks") or [] if isinstance(t, str) and t.strip()]
    why = str(payload.get("reason") or "").strip()
    if not wanted:
        return ToolOutcome(result="Give 1 to 3 track names from the session summary.")
    if ctx.live is None or ctx.summary is None:
        return ToolOutcome(result="Live isn't connected, so there are no tracks to read.")
    lines = []
    if len(wanted) > MAX_TRACKS_PER_READ:
        lines.append(f"Only the first {MAX_TRACKS_PER_READ} tracks were read; ask again for more.")
        wanted = wanted[:MAX_TRACKS_PER_READ]
    for name in wanted:
        index, candidates = resolve_track(name, ctx.summary)
        if index is None:
            if candidates:
                lines.append(f'"{name}" could be: ' + ", ".join(f'"{c}"' for c in candidates)
                             + ". Read again with the exact name.")
            else:
                lines.append(f'No track is called "{name}". Use a name from the session summary.')
            continue
        track = ctx.summary.track(index)
        if index in ctx.detail:
            lines.append(f'"{track.name}" already has full detail in the context.')
            continue
        if ctx.reads_left <= 0:
            lines.append(f'"{track.name}": {BUDGET_USED}')
            continue
        try:
            state = as_track_state(ctx.live.track_state(index))
        except Exception as e:  # noqa: BLE001 - tell the model, don't fail the turn
            log.warning("Couldn't read track %s for read_track", index, exc_info=True)
            lines.append(f'Couldn\'t read "{track.name}" from Live ({e}).')
            continue
        if state.track.index is None:
            state.track.index = index
        ctx.reads_left -= 1
        ctx.detail.add(index)
        ctx.looked.append(Look(index, state, why))
        lines += ["Full detail (read):", state.describe()]
    return ToolOutcome(result="\n".join(lines))


READ_TRACK = Tool(
    ToolSpec(
        name="read_track",
        description=(
            "Read the full device chain and parameters of 1 to 3 tracks that the context only "
            "summarizes. Use it when the answer depends on a track you can't see in full: the "
            "low end or mud (the kick and bass), masking, or whether two tracks clash. Don't "
            "read tracks the ask doesn't need, or ones that already have full detail."
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["tracks", "reason"],
            "properties": {
                "tracks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Track names exactly as in the session summary, 1 to 3.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why you need them, in a few words, e.g. \"check the kick's low end\".",
                },
            },
        },
    ),
    _read_track,
    kind="read",
)


def default_tools() -> ToolRegistry:
    return ToolRegistry([SUGGEST_STEPS, READ_TRACK])
