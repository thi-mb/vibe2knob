"""Chats: multi-turn conversations about the session, beside the one-shot `ask()`.

A chat owns its context (ContextSet: follow Live's selection, plus @-pinned tracks). Each
turn the engine reads Live afresh and builds the prompt from:
- the cached system prompt (the knowledge map),
- older turns as plain text (prose, plus the steps written out),
- the newest user message with a fresh <context> block: the light session summary, the
  full detail of the followed and pinned tracks, and what changed on those tracks since
  the last answer (the "refine" diff).

The answer is streamed prose plus an optional `suggest_steps` tool call (see tools.py).
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Callable, Iterator, Literal, Union

from pydantic import BaseModel, Field

from .advice import Step, format_step
from .knowledge import Term, load_terms, match_terms
from .prompt import build_chat_system_prompt, build_chat_user_message
from .protocol import ChatSummary, ContextSet
from .providers import Provider, ProviderError
from .providers.base import Done, Error, TextDelta, ToolCall
from .session import LiveView, SessionSummary, as_summary, as_track_state, describe_summary
from .state import Device, TrackState
from .tools import ToolContext, ToolRegistry, default_tools

CHAT_SCHEMA_VERSION = 1

# More changes than this are cut, so a big edit doesn't flood the prompt.
MAX_CHANGES = 25


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class UserTurn(BaseModel):
    role: Literal["user"] = "user"
    id: str = Field(default_factory=_new_id)
    text: str
    created_at: str = Field(default_factory=_now)


class AssistantTurn(BaseModel):
    role: Literal["assistant"] = "assistant"
    id: str = Field(default_factory=_new_id)
    text: str = ""
    steps: list[Step] = Field(default_factory=list)
    listen_for: str | None = None
    caution: str | None = None
    warnings: list[str] = Field(default_factory=list)
    snapshots: dict[str, TrackState] = Field(
        default_factory=dict,
        description="Full-detail tracks this answer saw, keyed by track index.",
    )
    usage: dict = Field(default_factory=dict)
    error: str | None = None
    created_at: str = Field(default_factory=_now)


Turn = Annotated[Union[UserTurn, AssistantTurn], Field(discriminator="role")]


class Chat(BaseModel):
    schema_version: Literal[1] = CHAT_SCHEMA_VERSION
    id: str = Field(default_factory=_new_id)
    title: str | None = None
    context: ContextSet = Field(default_factory=ContextSet)
    turns: list[Turn] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    def summary(self) -> ChatSummary:
        return ChatSummary(id=self.id, title=self.title, context=self.context,
                           updated_at=self.updated_at)

    def last_answer(self) -> AssistantTurn | None:
        for t in reversed(self.turns):
            if isinstance(t, AssistantTurn) and t.error is None:
                return t
        return None

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Chat":
        return cls.model_validate(migrate(json.loads(text)))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json())

    @classmethod
    def load(cls, path: str | Path) -> "Chat":
        return cls.from_json(Path(path).read_text())


# One function per old schema_version, each upgrading a dict to the next version.
MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


def migrate(data: dict) -> dict:
    version = data.get("schema_version", 1)
    if version > CHAT_SCHEMA_VERSION:
        raise ValueError(
            f"This chat was saved by a newer vibe2knob (schema {version}); please update."
        )
    while version < CHAT_SCHEMA_VERSION:
        data = MIGRATIONS[version](data)
        version = data["schema_version"]
    return data


# What ChatEngine.send yields. Every turn starts with TurnStarted and ends with TurnDone;
# a failed turn sends TurnError just before TurnDone. ChatTitled may follow TurnDone.


@dataclass
class TurnStarted:
    turn_id: str


@dataclass
class TurnText:
    turn_id: str
    text: str


@dataclass
class TurnSteps:
    turn_id: str
    steps: list[Step]
    listen_for: str | None
    caution: str | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class TurnError:
    turn_id: str
    message: str


@dataclass
class TurnDone:
    turn_id: str
    usage: dict = field(default_factory=dict)


@dataclass
class ChatTitled:
    title: str


ChatEvent = Union[TurnStarted, TurnText, TurnSteps, TurnError, TurnDone, ChatTitled]


@dataclass
class TurnContext:
    """What one turn sees: the summary, the full-detail tracks, and notes for the model."""

    summary: SessionSummary | None = None
    detail: list[tuple[str, int, TrackState]] = field(default_factory=list)  # (why, index, state)
    notes: list[str] = field(default_factory=list)

    def check_tracks(self) -> list[TrackState]:
        """Every track steps may land on: full states, then summary-only tracks by name."""
        out = [s for _, _, s in self.detail]
        seen = {i for _, i, _ in self.detail}
        if self.summary:
            out += [t.as_state() for t in self.summary.tracks if t.index not in seen]
        return out


def gather_context(context: ContextSet, live: LiveView | None) -> TurnContext:
    ctx = TurnContext()
    if live is None:
        if context.follow_selection or context.pinned:
            ctx.notes.append("Live isn't connected right now, so there are no tracks to look at.")
        return ctx
    try:
        ctx.summary = as_summary(live.session_summary())
    except Exception as e:  # noqa: BLE001 - Live being away must not break the chat
        ctx.notes.append(f"Couldn't read the session from Live ({e}).")
    wanted: dict[int, list[str]] = {}
    if context.follow_selection:
        try:
            selected = live.selected_track_index()
        except Exception:  # noqa: BLE001
            selected = None
        if selected is not None:
            wanted.setdefault(selected, []).append("selected in Live")
    for index in context.pinned:
        wanted.setdefault(index, []).append("pinned")
    for index, why in wanted.items():
        try:
            state = as_track_state(live.track_state(index))
        except Exception as e:  # noqa: BLE001
            ctx.notes.append(f"Couldn't read track {index} from Live ({e}).")
            continue
        if state.track.index is None:
            state.track.index = index
        ctx.detail.append((" and ".join(why), index, state))
    return ctx


def diff_tracks(old: TrackState, new: TrackState) -> list[str]:
    """What the user changed on a track, as short lines for the prompt."""
    changes = []
    if old.track.name != new.track.name:
        changes.append(f'renamed the track to "{new.track.name}"')
    before, after = _flat_devices(old), _flat_devices(new)
    for key in after.keys() - before.keys():
        changes.append(f"added {key}")
    for key in before.keys() - after.keys():
        changes.append(f"removed {key}")
    top_before = [k for k in before if " > " not in k and k in after]
    top_after = [k for k in after if " > " not in k and k in before]
    if top_before != top_after:
        changes.append("reordered the chain: " + ", ".join(top_after))
    for key in [k for k in after if k in before]:
        a, b = before[key], after[key]
        if a.enabled != b.enabled:
            changes.append(f"turned {key} {'on' if b.enabled else 'off'}")
        old_params = {p.name: p.shown() for p in a.parameters if p.name != "Device On"}
        for p in b.parameters:
            if p.name in old_params and old_params[p.name] != p.shown():
                changes.append(f"{key} > {p.name}: {old_params[p.name]} -> {p.shown()}")
    om, nm = old.track.mixer, new.track.mixer
    if om and nm:
        if om.volume is not None and nm.volume is not None and abs(om.volume - nm.volume) > 0.005:
            changes.append(f"moved the fader ({om.volume:.2f} -> {nm.volume:.2f}, raw 0..1)")
        if om.panning is not None and nm.panning is not None and abs(om.panning - nm.panning) > 0.005:
            changes.append(f"panned the track ({om.panning:+.2f} -> {nm.panning:+.2f})")
        if om.mute != nm.mute and nm.mute is not None:
            changes.append("muted the track" if nm.mute else "unmuted the track")
    if len(changes) > MAX_CHANGES:
        changes = changes[:MAX_CHANGES] + [f"... and {len(changes) - MAX_CHANGES} more changes"]
    return changes


def _flat_devices(state: TrackState) -> dict[str, Device]:
    """Every device keyed by its place: 'Drum Rack > Kick > Simpler'. Duplicates get #2, #3."""
    out: dict[str, Device] = {}

    def walk(devices: list[Device], prefix: str) -> None:
        seen: Counter[str] = Counter()
        for d in devices:
            seen[d.name] += 1
            key = prefix + (d.name if seen[d.name] == 1 else f"{d.name} #{seen[d.name]}")
            out[key] = d
            for c in d.chains:
                walk(c.devices, f"{key} > {c.name} > ")

    walk(state.track.devices, "")
    return out


def render_answer(turn: AssistantTurn) -> str:
    """An earlier answer as plain text for the history: the prose, then its steps."""
    parts = [turn.text.strip()]
    if turn.steps:
        lines = ["[Steps I suggested:"] + [format_step(i, s) for i, s in enumerate(turn.steps, 1)]
        if turn.listen_for:
            lines.append(f"Listen: {turn.listen_for}")
        if turn.caution:
            lines.append(f"Watch out: {turn.caution}")
        parts.append("\n".join(lines) + "]")
    return "\n\n".join(p for p in parts if p)


TITLE_SYSTEM = (
    "You name chat tabs. Reply with only a topic title of two to five plain words in sentence "
    "case, with no quotes and no full stop, e.g. Wider vocal without mud."
)


class ChatEngine:
    def __init__(self, provider: Provider, terms: list[Term] | None = None,
                 tools: ToolRegistry | None = None):
        self.provider = provider
        self.terms = terms if terms is not None else load_terms()
        self.tools = tools or default_tools()
        self.system = build_chat_system_prompt(self.terms)

    def build_messages(self, chat: Chat, request: str, ctx: TurnContext) -> list[dict]:
        """The history as text, then the newest user message with a fresh context block."""
        messages = []
        for t in chat.turns:
            if isinstance(t, UserTurn):
                messages.append({"role": "user", "content": t.text})
            elif t.error is None or t.text:
                messages.append({"role": "assistant", "content": render_answer(t)})
        detail_why = {i: why for why, i, _ in ctx.detail}
        summary = describe_summary(ctx.summary, detail_why) if ctx.summary else None
        changes = []
        previous = chat.last_answer()
        if previous:
            for _, index, state in ctx.detail:
                old = previous.snapshots.get(str(index))
                if old is not None:
                    changes += [f'"{state.track.name}": {c}' for c in diff_tracks(old, state)]
        content = build_chat_user_message(
            request,
            match_terms(request, self.terms),
            summary,
            [(why, state) for why, _, state in ctx.detail],
            changes,
            ctx.notes,
        )
        messages.append({"role": "user", "content": content})
        return messages

    def send(self, chat: Chat, text: str, live: LiveView | None = None) -> Iterator[ChatEvent]:
        """Run one turn: read Live, stream the answer, and append both turns to `chat`."""
        ctx = gather_context(chat.context, live)
        messages = self.build_messages(chat, text, ctx)
        chat.turns.append(UserTurn(text=text))
        turn = AssistantTurn(snapshots={str(i): s for _, i, s in ctx.detail})
        yield TurnStarted(turn.id)
        only = ctx.detail[0][2] if len(ctx.detail) == 1 else None
        tool_ctx = ToolContext(ctx.check_tracks(), only)
        prose: list[str] = []
        for event in self._stream(messages):
            if isinstance(event, TextDelta):
                prose.append(event.text)
                yield TurnText(turn.id, event.text)
            elif isinstance(event, ToolCall):
                outcome = self.tools.handle(event.name, event.input, tool_ctx)
                turn.warnings += outcome.warnings
                if outcome.steps is not None and outcome.steps.steps:
                    turn.steps = outcome.steps.steps
                    turn.listen_for = outcome.steps.listen_for
                    turn.caution = outcome.steps.caution
                    yield TurnSteps(turn.id, turn.steps, turn.listen_for, turn.caution,
                                    list(outcome.warnings))
            elif isinstance(event, Done):
                turn.usage = event.usage
            elif isinstance(event, Error):
                turn.error = event.message
                yield TurnError(turn.id, event.message)
        turn.text = "".join(prose)
        chat.turns.append(turn)
        chat.updated_at = _now()
        yield TurnDone(turn.id, turn.usage)
        if chat.title is None and turn.error is None:
            title = self.title_for(text, turn.text)
            if title:
                chat.title = title
                yield ChatTitled(title)

    def _stream(self, messages: list[dict]) -> Iterator:
        try:
            yield from self.provider.stream_chat(self.system, messages, self.tools.specs())
        except ProviderError as e:
            yield Error(str(e))

    def title_for(self, request: str, answer: str) -> str | None:
        """A short topic title from one cheap call. None if the call fails."""
        try:
            raw = self.provider.generate_text(
                TITLE_SYSTEM, f"User: {request}\n\nAssistant: {answer[:800]}"
            )
        except Exception:  # noqa: BLE001 - a missing title must never fail the chat
            return None
        title = (raw or "").strip().splitlines()[0].strip().strip("\"'").rstrip(".") if raw else ""
        return title[:60] or None
