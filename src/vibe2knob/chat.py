"""Chats: multi-turn conversations about the session, beside the one-shot `ask()`.

A chat owns its context (ContextSet: follow Live's selection, plus @-pinned tracks). Each
turn the engine reads Live afresh and builds the prompt from:
- the cached system prompt (the knowledge map),
- older turns as plain text (prose, plus the steps written out),
- the newest user message with a fresh <context> block: the light session summary, the
  full detail of the followed and pinned tracks, and what changed on those tracks since
  the last answer (the "refine" diff).

The answer is streamed prose plus an optional `suggest_steps` tool call (see tools.py).
When the model calls a read tool (`read_track`), the engine runs it, sends the result back
and calls the model again, up to a read budget per turn; the prose of every round joins
into one answer.
"""

from __future__ import annotations

import json
import logging
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
from .protocol import ChatSummary, ContextSet, TrackRef
from .providers import Provider, ProviderError
from .providers.base import Done, Error, Phase, TextDelta, ToolCall
from .session import (LiveView, SessionSummary, as_summary, as_track_state, describe_summary,
                      read_arrangement)
from .state import Device, TrackState, TrackType
from .tools import BUDGET_USED, Look, ToolContext, ToolOutcome, ToolRegistry, default_tools
from .usage import Sizes, UsageRecord, split_parts

log = logging.getLogger("vibe2knob.chat")

CHAT_SCHEMA_VERSION = 1

# More changes than this are cut, so a big edit doesn't flood the prompt.
MAX_CHANGES = 25

# The read budget of one turn: model rounds that may read, and tracks read in all.
MAX_READ_ROUNDS = 3
MAX_READS = 6


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class UserTurn(BaseModel):
    role: Literal["user"] = "user"
    id: str = Field(default_factory=_new_id)
    text: str
    created_at: str = Field(default_factory=_now)


class LookedAt(BaseModel):
    """A track the model read during a turn."""

    index: int
    name: str
    type: TrackType
    why: str = ""

    def ref(self) -> TrackRef:
        return TrackRef(index=self.index, name=self.name, type=self.type)


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
    looked_at: list[LookedAt] = Field(
        default_factory=list, description="Tracks the model read with read tools."
    )
    usage: dict = Field(
        default_factory=dict,
        description="The provider's raw counts, summed over the turn's rounds (`rounds`).",
    )
    parts: dict[str, int] = Field(
        default_factory=dict, description="The usage split into parts (see usage.py)."
    )
    error: str | None = None
    created_at: str = Field(default_factory=_now)

    def usage_record(self, chat_id: str | None = None, provider: str | None = None,
                     model: str | None = None) -> UsageRecord:
        return UsageRecord.from_raw(self.usage, chat_id=chat_id, turn_id=self.id,
                                    provider=provider, model=model, at=self.created_at,
                                    parts=self.parts)


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
# TurnStatus says what the turn is on while there's nothing to show yet.


@dataclass
class TurnStarted:
    turn_id: str


@dataclass
class TurnStatus:
    turn_id: str
    phase: str  # "reading" (Live), "thinking" (waiting for the model) or "steps"
    detail: str | None = None  # e.g. "Reading Kick, Sub Bass" when the model reads tracks


@dataclass
class TurnLooked:
    """Tracks the model just read (only the new ones)."""

    turn_id: str
    tracks: list[TrackRef]


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
    record: UsageRecord | None = None  # None when the model never answered


@dataclass
class ChatTitled:
    title: str
    record: UsageRecord | None = None  # what naming the chat used


ChatEvent = Union[TurnStarted, TurnStatus, TurnLooked, TurnText, TurnSteps, TurnError, TurnDone,
                  ChatTitled]


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
    if ctx.summary is not None:
        try:
            ctx.summary.arrangement = read_arrangement(live)
        except Exception:  # noqa: BLE001 - without it the model just doesn't know timing
            log.warning("Couldn't read the arrangement from Live", exc_info=True)
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
    if turn.looked_at:
        parts.append("(looked at " + ", ".join(t.name for t in turn.looked_at) + ")")
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
        """Run one turn: read Live, stream the answer, and append both turns to `chat`.

        The model may call read tools; then the engine runs them, appends the results and
        calls it again, until a round reads nothing. The read budget (MAX_READ_ROUNDS rounds,
        MAX_READS tracks) is told to the model as a tool result, so the tool list and the
        cached prefix never change within a turn.
        """
        turn = AssistantTurn()
        yield TurnStarted(turn.id)
        yield TurnStatus(turn.id, "reading")
        ctx = gather_context(chat.context, live)
        turn.snapshots = {str(i): s for _, i, s in ctx.detail}
        messages = self.build_messages(chat, text, ctx)
        chat.turns.append(UserTurn(text=text))
        yield TurnStatus(turn.id, "thinking")
        only = ctx.detail[0][2] if len(ctx.detail) == 1 else None
        tool_ctx = ToolContext(ctx.check_tracks(), only, live=live, summary=ctx.summary,
                               detail={i for _, i, _ in ctx.detail}, reads_left=MAX_READS)
        prose: list[str] = []
        steps_chars = calls_chars = 0
        usages: list[dict] = []
        rounds: list[list[dict]] = []
        read_rounds = 0
        last_call = MAX_READ_ROUNDS + 1  # one more round to answer after the budget is used
        for call in range(last_call + 1):
            rounds.append(messages)
            round_text: list[str] = []
            tool_calls: list[ToolCall] = []
            done: Done | None = None
            for event in self._stream(messages):
                if isinstance(event, TextDelta):
                    if not round_text and prose and not prose[-1][-1:].isspace():
                        prose.append("\n\n")
                        yield TurnText(turn.id, "\n\n")
                    round_text.append(event.text)
                    prose.append(event.text)
                    yield TurnText(turn.id, event.text)
                elif isinstance(event, ToolCall):
                    if event.id is None:
                        event.id = f"call_{call}_{len(tool_calls)}"
                    tool_calls.append(event)
                elif isinstance(event, Phase):
                    if self.tools.kind(event.tool) != "read":
                        yield TurnStatus(turn.id, event.name)
                elif isinstance(event, Done):
                    done = event
                    usages.append(event.usage)
                elif isinstance(event, Error):
                    turn.error = event.message
                    yield TurnError(turn.id, event.message)
            if turn.error is not None:
                break
            results: dict[str, str] = {}
            reads = []
            for c in tool_calls:
                if self.tools.kind(c.name) == "read":
                    reads.append(c)
                    calls_chars += len(json.dumps(c.input))
                    continue
                steps_chars += len(json.dumps(c.input))
                outcome = self.tools.handle(c.name, c.input, tool_ctx)
                turn.warnings += outcome.warnings
                results[c.id] = outcome.result or "Shown to the user."
                if outcome.steps is not None and outcome.steps.steps:
                    turn.steps = outcome.steps.steps
                    turn.listen_for = outcome.steps.listen_for
                    turn.caution = outcome.steps.caution
                    yield TurnSteps(turn.id, turn.steps, turn.listen_for, turn.caution,
                                    list(outcome.warnings))
            if not reads or call == last_call:
                break
            over = read_rounds >= MAX_READ_ROUNDS
            read_rounds += 0 if over else 1
            names = [n for c in reads for n in c.input.get("tracks") or [] if isinstance(n, str)]
            if names and not over and tool_ctx.reads_left > 0:
                yield TurnStatus(turn.id, "reading", "Reading " + ", ".join(names))
            seen = len(tool_ctx.looked)
            for c in reads:
                outcome = (ToolOutcome(result=BUDGET_USED) if over
                           else self.tools.handle(c.name, c.input, tool_ctx))
                turn.warnings += outcome.warnings
                results[c.id] = outcome.result or ""
            new = tool_ctx.looked[seen:]
            if new:
                self._looked(turn, tool_ctx, ctx, new)
                yield TurnLooked(turn.id, [_ref(look) for look in new])
            messages = messages + [
                {"role": "assistant", "content": "".join(round_text),
                 "tool_calls": [{"id": c.id, "name": c.name, "input": c.input}
                                for c in tool_calls],
                 "raw": done.raw if done else None},
                {"role": "user", "content": "",
                 "tool_results": [{"id": c.id, "name": c.name, "content": results[c.id]}
                                  for c in tool_calls]},
            ]
            yield TurnStatus(turn.id, "thinking")
        turn.text = "".join(prose)
        turn.usage = _sum_usage(usages)
        record = None
        if turn.usage:
            sizes = self._sizes(rounds, text)
            sizes.answer, sizes.steps = len(turn.text) + calls_chars, steps_chars
            record = turn.usage_record(chat.id, self.provider.name,
                                       getattr(self.provider, "model", None))
            turn.parts = record.parts = split_parts(record, sizes)
        chat.turns.append(turn)
        chat.updated_at = _now()
        yield TurnDone(turn.id, turn.usage, record)
        if chat.title is None and turn.error is None:
            title = self.title_for(text, turn.text)
            if title:
                chat.title = title
                yield ChatTitled(title, self._last_record("title", chat.id))

    @staticmethod
    def _looked(turn: AssistantTurn, tool_ctx: ToolContext, ctx: TurnContext,
                new: list[Look]) -> None:
        """Tracks just read join the snapshots, the looked-at list and the step check."""
        for look in new:
            turn.snapshots[str(look.index)] = look.state
            turn.looked_at.append(LookedAt(index=look.index, name=look.state.track.name,
                                           type=look.state.track.type, why=look.why))
        full = [s for _, _, s in ctx.detail] + [look.state for look in tool_ctx.looked]
        known = {s.track.index for s in full}
        tool_ctx.tracks = full + [s for s in tool_ctx.tracks if s.track.index not in known]

    def _sizes(self, rounds: list[list[dict]], request: str) -> Sizes:
        """How long each part of the prompt was over the turn's rounds, plus exact counts of
        the instructions and tools if the provider has them.

        Each round resends the instructions, tools and history. What it adds after the
        user's message (earlier rounds and read results) counts as live.
        """
        first = rounds[0]
        newest = len(first) - 1  # the user's message with the context block
        sizes = Sizes()
        for messages in rounds:
            sizes.instructions += len(self.system)
            sizes.tools += len(json.dumps([s.input_schema for s in self.tools.specs()]))
            sizes.history += sum(_length(m) for m in messages[:newest])
            sizes.message += len(request)
            sizes.live += max(0, _length(messages[newest]) - len(request))
            sizes.live += sum(_length(m) for m in messages[newest + 1:])
        count_parts = getattr(self.provider, "count_parts", None)
        if count_parts is not None:
            try:
                exact = count_parts(self.system, self.tools.specs()) or {}
            except Exception:  # noqa: BLE001 - the split falls back to estimates
                exact = {}
            sizes.exact = {k: v * len(rounds) for k, v in exact.items()}
        return sizes

    def _last_record(self, kind: str, chat_id: str | None) -> UsageRecord | None:
        """The usage of the provider's last generate_text call, if it reported one."""
        raw = getattr(self.provider, "last_usage", None)
        if not raw:
            return None
        return UsageRecord.from_raw(raw, kind=kind, chat_id=chat_id, provider=self.provider.name,
                                    model=getattr(self.provider, "model", None))

    def _stream(self, messages: list[dict]) -> Iterator:
        try:
            yield from self.provider.stream_chat(self.system, messages, self.tools.specs())
        except ProviderError as e:
            yield Error(str(e))

    def title_for(self, request: str, answer: str) -> str | None:
        """A short topic title from one cheap call. None if the call fails."""
        if hasattr(self.provider, "last_usage"):
            self.provider.last_usage = None
        try:
            raw = self.provider.generate_text(
                TITLE_SYSTEM, f"User: {request}\n\nAssistant: {answer[:800]}"
            )
        except Exception:  # noqa: BLE001 - a missing title must never fail the chat
            return None
        title = (raw or "").strip().splitlines()[0].strip().strip("\"'").rstrip(".") if raw else ""
        return title[:60] or None


def _length(message: dict) -> int:
    """A message's length in characters, tool calls and results included."""
    return (len(message["content"])
            + sum(len(r["content"]) for r in message.get("tool_results", ()))
            + sum(len(json.dumps(c["input"])) for c in message.get("tool_calls", ())))


def _sum_usage(usages: list[dict]) -> dict:
    """The provider's counts summed over a turn's rounds; `model` is the last one's."""
    usages = [u for u in usages if u]
    if not usages:
        return {}
    out: dict = {}
    for u in usages:
        for k, v in u.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = out.get(k, 0) + v
            else:
                out[k] = v
    out["rounds"] = len(usages)
    return out


def _ref(look: Look) -> TrackRef:
    t = look.state.track
    return TrackRef(index=look.index, name=t.name, type=t.type, color=t.color,
                    devices=[d.name for d in t.devices])
