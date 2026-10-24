"""Tools the chat model may call.

- `suggest_steps` ("answer"): the optional structured part of an answer.
- `read_track` ("read"): the full detail of tracks the model only knows from the summary.
- `read_arrangement` ("read"): who plays in a range of bars or at a locator, with clip names.
- `read_scenes` ("read"): the clips in Session view scenes, or what plays from it now.

A read tool's result goes back to the model, which answers in the next round (see
ChatEngine.send). Each read spends one of the turn's reads (one per track for read_track).

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
from .arrangement import Timeline, describe_range, find_locator
from .core import check_steps
from .providers.base import ToolSpec
from .session import LiveView, SessionSummary, as_track_state
from .session_view import (MAX_SCENES_PER_READ, Grid, describe_playing, describe_scene,
                           find_scene)
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
    # For read tools: what a call reads, for the progress line ("Reading <this>").
    status: Callable[[dict], str | None] | None = None


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

    def status(self, name: str, payload: dict) -> str | None:
        tool = self._tools.get(name)
        if tool is None or tool.status is None:
            return None
        try:
            return tool.status(payload)
        except Exception:  # noqa: BLE001 - a malformed call just shows no detail
            return None

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
            "and a track in the context fits. Don't call it for questions or explanations. "
            "Every step changes a setting; an A/B or bypass check goes in listen_for, never "
            "in a step."
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
    status=lambda p: ", ".join(n for n in p.get("tracks") or [] if isinstance(n, str)) or None,
)


def _bar(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _read_arrangement(payload: dict, ctx: ToolContext) -> ToolOutcome:
    if ctx.summary is None:
        return ToolOutcome(result="Live isn't connected, so there's no arrangement to read.")
    if ctx.summary.arrangement is None:
        return ToolOutcome(result="The Live link can't read the arrangement in this set.")
    timeline = Timeline(ctx.summary.arrangement)
    locator = str(payload.get("locator") or "").strip()
    first, last = _bar(payload.get("from_bar")), _bar(payload.get("to_bar"))
    name = None
    if locator:
        section, candidates = find_locator(timeline, locator)
        if section is None:
            if not timeline.locator_sections():
                return ToolOutcome(result="The set has no locators. Give from_bar and to_bar "
                                          "instead (the Arrangement part lists sections by bar).")
            if candidates:
                return ToolOutcome(result=f'"{locator}" could be: '
                                   + ", ".join(f'"{c}"' for c in candidates)
                                   + ". Read again with the exact name.")
            return ToolOutcome(result=f'No locator is called "{locator}". The locators are: '
                               + ", ".join(f'"{s.name}"' for s in timeline.locator_sections())
                               + ".")
        first, last, name = section.first, section.last, section.name
    elif first is None and last is None:
        return ToolOutcome(result="Give from_bar and to_bar, or a locator name.")
    if ctx.reads_left <= 0:
        return ToolOutcome(result=BUDGET_USED)
    ctx.reads_left -= 1
    names = {t.index: t.name for t in ctx.summary.tracks}
    first = 1 if first is None else first
    last = timeline.last_bar if last is None else last
    return ToolOutcome(result=describe_range(timeline, names, first, last, name))


def _arrangement_status(p: dict) -> str:
    if p.get("locator"):
        return f"the arrangement at {str(p['locator']).strip()}"
    first, last = _bar(p.get("from_bar")), _bar(p.get("to_bar"))
    if first is not None and last is not None:
        return f"the arrangement, bars {first}-{last}" if first != last else f"the arrangement, bar {first}"
    return "the arrangement"


READ_ARRANGEMENT = Tool(
    ToolSpec(
        name="read_arrangement",
        description=(
            "Read who plays in a stretch of Arrangement view: every track with clips there, "
            "in which bars, its clip names, and where tracks come in and drop out. Give a "
            "range of bars, or a locator name. Use it when the ask is about a part of the song "
            "(\"the drop\", \"the second verse\", bars 33-48) and the Arrangement part of the "
            "context doesn't list everyone who plays there."
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["from_bar", "to_bar", "locator", "reason"],
            "properties": {
                "from_bar": {"anyOf": [{"type": "integer"}, {"type": "null"}],
                             "description": "First bar, numbered from 1 as Live shows it, or null."},
                "to_bar": {"anyOf": [{"type": "integer"}, {"type": "null"}],
                           "description": "Last bar (included), or null for the song's end."},
                "locator": {"anyOf": [{"type": "string"}, {"type": "null"}],
                            "description": "A locator name from the Arrangement part, instead of "
                                           "bars; null when giving bars."},
                "reason": {
                    "type": "string",
                    "description": "Why you need it, in a few words, e.g. \"who plays in the drop\".",
                },
            },
        },
    ),
    _read_arrangement,
    kind="read",
    status=_arrangement_status,
)


PLAYING = {"playing", "playing now", "now", "what's playing", "what plays now"}


def _read_scenes(payload: dict, ctx: ToolContext) -> ToolOutcome:
    wanted = [str(s).strip() for s in payload.get("scenes") or []
              if isinstance(s, (str, int)) and not isinstance(s, bool) and str(s).strip()]
    if not wanted:
        return ToolOutcome(result='Give 1 to 4 scene names or numbers, or "playing".')
    if ctx.summary is None:
        return ToolOutcome(result="Live isn't connected, so there's no Session view to read.")
    if ctx.summary.session is None:
        return ToolOutcome(result="The Live link can't read Session view in this set.")
    grid = Grid(ctx.summary.session)
    if not grid.has_clips:
        return ToolOutcome(result="Session view has no clips in this set.")
    if ctx.reads_left <= 0:
        return ToolOutcome(result=BUDGET_USED)
    names = {t.index: t.name for t in ctx.summary.tracks}
    bpb = ctx.summary.arrangement.beats_per_bar if ctx.summary.arrangement else 4.0
    parts = []
    if len(wanted) > MAX_SCENES_PER_READ:
        parts.append(f"Only the first {MAX_SCENES_PER_READ} were read; ask again for more.")
        wanted = wanted[:MAX_SCENES_PER_READ]
    read = False
    for ref in wanted:
        if ref.casefold() in PLAYING:
            parts.append(describe_playing(grid, names, bpb))
            read = True
            continue
        index, candidates = find_scene(grid, ref)
        if index is None:
            if candidates:
                parts.append(f'"{ref}" could be: ' + ", ".join(candidates)
                             + ". Read again with the number.")
            else:
                named = [s.label() for s in grid.scenes if s.name.strip()][:12]
                parts.append(f'No scene matches "{ref}". There are {len(grid.scenes)}, numbered '
                             "from 1" + (f"; the named ones: {', '.join(named)}." if named else "."))
            continue
        parts.append(describe_scene(grid, names, index, bpb))
        read = True
    if read:
        ctx.reads_left -= 1
    return ToolOutcome(result="\n".join(parts))


def _scenes_status(p: dict) -> str:
    wanted = [str(s).strip() for s in p.get("scenes") or [] if str(s).strip()]
    if wanted and all(w.casefold() in PLAYING for w in wanted):
        return "what plays now"
    shown = [w for w in wanted if w.casefold() not in PLAYING]
    return ("scene " if len(shown) == 1 else "scenes ") + ", ".join(shown)


READ_SCENES = Tool(
    ToolSpec(
        name="read_scenes",
        description=(
            "Read Session view scenes: each track's clip in the scene (name and length) and "
            "whether it plays now. Give scene names or numbers (from 1), or \"playing\" for "
            "every clip playing from Session view now. Use it when the ask is about a scene "
            "(\"the drop scene\", scene 4) or what they hear now, and the Session view part "
            "of the context doesn't show enough."
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["scenes", "reason"],
            "properties": {
                "scenes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "1 to 4 scene names or numbers, or \"playing\".",
                },
                "reason": {
                    "type": "string",
                    "description": "Why you need them, in a few words, e.g. \"what's in the drop\".",
                },
            },
        },
    ),
    _read_scenes,
    kind="read",
    status=_scenes_status,
)


def default_tools() -> ToolRegistry:
    return ToolRegistry([SUGGEST_STEPS, READ_TRACK, READ_ARRANGEMENT, READ_SCENES])
