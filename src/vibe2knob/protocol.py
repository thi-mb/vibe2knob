"""The hub <-> client protocol (v1).

Every surface (the desktop panel, a browser tab, later a phone) talks to the hub over one
WebSocket with these JSON messages. This module is the single source of truth: the UI's
TypeScript types are generated from `schema()` (see ui/package.json, `gen:types`), and CI
fails if the two drift apart.

Rules for changing it:
- Adding an optional field or a new message type is fine within a version.
- Renaming, removing or retyping a field means bumping PROTOCOL_VERSION.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel
from pydantic.json_schema import models_json_schema

from .advice import Step

PROTOCOL_VERSION = 1


class _Message(BaseModel):
    # Defaults count as required in the hub's (serialization) schema, since the hub always
    # sends them; senders may still leave them out.
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    v: Literal[1] = Field(default=PROTOCOL_VERSION, description="Protocol version.")


class _Request(_Message):
    request_id: str | None = Field(
        default=None, description="Set by the client; echoed on the hub's reply or error."
    )


# Shared shapes


class _Shape(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class TrackRef(_Shape):
    """A track as the session list and the context chips show it."""

    index: int
    name: str
    type: Literal["audio", "midi", "group", "return", "master"]
    color: int | None = None
    devices: list[str] = Field(default_factory=list, description="Device names in chain order.")


class ContextSet(_Shape):
    """What a chat looks at, besides the light session summary it always gets."""

    follow_selection: bool = Field(
        default=True, description="Include whichever track is selected in Live."
    )
    pinned: list[int] = Field(default_factory=list, description="Track indexes pinned with @.")


UsagePart = Literal["instructions", "tools", "live", "history", "message", "thinking", "answer",
                    "steps"]


class Usage(_Shape):
    """Tokens used by one turn, one chat or a period. Counted on the hub, never sent anywhere.

    input_tokens is the uncached input only: the whole prompt is input + cache_read +
    cache_write. thinking_tokens are part of output_tokens.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    thinking_tokens: int = 0
    calls: int = Field(default=0, description="Model calls counted; each one resends the instructions.")
    parts: dict[UsagePart, int] = Field(
        default_factory=dict,
        description="Prompt and answer split into parts. Only instructions, tools and thinking "
        "are exact; the rest are estimates by text length.",
    )
    model: str | None = Field(default=None, description="The model that answered; null if several.")
    cost_usd: float | None = Field(
        default=None, description="Estimated from the hub's price table; null if no price is known."
    )
    cost_complete: bool = Field(
        default=True, description="False when some calls had no known price, so cost_usd is low."
    )
    prompt_cost_usd: float | None = Field(
        default=None, description="The part of cost_usd spent on prompts (input and cache)."
    )
    output_cost_usd: float | None = Field(
        default=None, description="The part of cost_usd spent on output, thinking included."
    )


ChatMode = Literal["teach", "do"]

ActionKind = Literal["mute", "solo", "volume", "pan", "send", "device_on", "parameter"]

ActionStatus = Literal["proposed", "applied", "skipped", "failed", "undone"]


class Action(_Shape):
    """One change to Live that Do mode proposes. Nothing is applied until the user confirms
    it (or its kind is in Settings.auto_actions), and an applied action can be undone."""

    id: str
    kind: ActionKind
    track: TrackRef
    device: str | None = Field(default=None, description="For device_on and parameter.")
    send: str | None = Field(
        default=None, description='For send: the return track\'s name, e.g. "A-Reverb".'
    )
    parameter: str | None = Field(default=None, description="For parameter.")
    before: str | None = Field(
        default=None, description="The value as Live showed it before, e.g. \"-6.0 dB\"; null if "
        "unknown."
    )
    after: str = Field(description="The value to set, as Live would show it, e.g. \"-3.0 dB\".")
    status: ActionStatus = "proposed"
    note: str | None = Field(
        default=None, description="Why it failed or was skipped, or what the hub corrected."
    )


class VoiceStatus(_Shape):
    """Whether the hub can transcribe speech."""

    available: bool = False
    reason: str | None = Field(
        default=None, description="Why voice isn't available, e.g. vibe2knob[voice] not installed."
    )
    model: str | None = Field(default=None, description="The speech model, e.g. \"base.en\".")


class ChatSummary(_Shape):
    """A chat as a tab shows it."""

    id: str
    title: str | None = Field(default=None, description="Topic title; null until named.")
    context: ContextSet
    updated_at: str = Field(description="ISO 8601 time of the last change.")
    usage: Usage | None = Field(
        default=None,
        description="Everything the chat used, titles included. Its model is the answers' model.",
    )
    title_usage: Usage | None = Field(default=None, description="What naming the chat used.")
    context_tokens: int | None = Field(
        default=None, description="Tokens the next message resends: the last answer's prompt plus "
        "its output.",
    )
    context_window: int | None = Field(default=None, description="The model's context window.")
    mode: ChatMode = Field(
        default="teach", description="teach: steps the user does. do: the hub proposes actions."
    )


class LiveStatus(_Shape):
    connected: bool
    selected_track: TrackRef | None = None
    detail: str | None = Field(default=None, description="Why Live isn't reachable, if it isn't.")
    example: str | None = Field(
        default=None, description="Name of the example track in use instead of Live, if any."
    )
    can_write: bool = Field(
        default=False, description="True when the hub can change Live (Do mode's actions)."
    )


class ChatTurn(_Shape):
    """One turn of a saved chat, as chat.history replays it."""

    id: str = Field(description="For an assistant turn, the turn_id its deltas carried.")
    role: Literal["user", "assistant"]
    text: str
    created_at: str = Field(description="ISO 8601 time the turn started.")
    steps: list[Step] | None = Field(default=None, description="Null when no steps were suggested.")
    listen_for: str | None = None
    caution: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = Field(default=None, description="Why the turn failed, if it did.")
    usage: Usage | None = Field(default=None, description="Set on assistant turns.")
    looked_at: list[TrackRef] = Field(
        default_factory=list, description="Tracks the model read during an assistant turn."
    )
    actions: list[Action] = Field(
        default_factory=list, description="Do mode actions of an assistant turn, latest status."
    )


class Settings(_Shape):
    """Settings as the client sees them. The API key itself never leaves the hub."""

    provider: Literal["claude", "ollama"] = "claude"
    model: str | None = None
    api_key_set: bool = False
    monthly_budget_usd: float | None = Field(
        default=None, description="Warn once this month's spend passes it; null is off."
    )
    default_mode: ChatMode = Field(default="teach", description="The mode new chats start in.")
    auto_actions: list[ActionKind] = Field(
        default_factory=list, description="Action kinds applied without asking first."
    )


# Client -> hub


class Hello(_Request):
    type: Literal["hello"] = "hello"
    client: Literal["panel", "browser", "phone"]


class ChatCreate(_Request):
    type: Literal["chat.create"] = "chat.create"
    context: ContextSet = Field(default_factory=ContextSet)


class ChatSend(_Request):
    type: Literal["chat.send"] = "chat.send"
    chat_id: str
    text: str


class ChatUpdate(_Request):
    type: Literal["chat.update"] = "chat.update"
    chat_id: str
    title: str | None = None
    context: ContextSet | None = None


class ChatOpen(_Request):
    """Ask for a chat's turns, e.g. when a tab is shown after a reload or reconnect."""

    type: Literal["chat.open"] = "chat.open"
    chat_id: str


class ChatClose(_Request):
    type: Literal["chat.close"] = "chat.close"
    chat_id: str


class TracksList(_Request):
    type: Literal["tracks.list"] = "tracks.list"


class SettingsUpdate(_Request):
    type: Literal["settings.update"] = "settings.update"
    provider: Literal["claude", "ollama"] | None = None
    model: str | None = None
    api_key: str | None = Field(default=None, description="Stored in the Keychain, never echoed.")
    monthly_budget_usd: float | None = Field(
        default=None, description="A new monthly limit; 0 or less turns it off."
    )
    default_mode: ChatMode | None = None
    auto_actions: list[ActionKind] | None = Field(
        default=None, description="Replaces the whole list; [] asks before every action."
    )


class SettingsTest(_Request):
    type: Literal["settings.test"] = "settings.test"


class ChatSetMode(_Request):
    """Switch a chat between Teach and Do. The hub answers with chat.changed."""

    type: Literal["chat.set_mode"] = "chat.set_mode"
    chat_id: str
    mode: ChatMode


class _ActionsRequest(_Request):
    chat_id: str
    action_ids: list[str] = Field(min_length=1)


class ActionConfirm(_ActionsRequest):
    """Apply proposed actions. The hub answers with chat.actions."""

    type: Literal["action.confirm"] = "action.confirm"


class ActionSkip(_ActionsRequest):
    """Leave proposed actions unapplied."""

    type: Literal["action.skip"] = "action.skip"


class ActionUndo(_ActionsRequest):
    """Put applied actions back, if Live still holds the values the hub set."""

    type: Literal["action.undo"] = "action.undo"


class StepApply(_Request):
    """Turn a step card into an action (the Apply button on an on-track step)."""

    type: Literal["step.apply"] = "step.apply"
    chat_id: str
    turn_id: str
    step_index: int = Field(ge=0, description="Index into the turn's steps.")


class VoiceTranscribe(_Request):
    """Recorded speech to turn into text. The hub answers with voice.transcript."""

    type: Literal["voice.transcribe"] = "voice.transcribe"
    request_id: str = Field(description="Echoed on the voice.transcript.")
    audio: str = Field(description="The recording, base64.")
    mime: str = Field(description='e.g. "audio/webm" or "audio/wav".')


UsageRange = Literal["today", "7d", "30d", "all"]


class UsageGet(_Request):
    """Ask for the usage report, e.g. when the Usage section in Settings opens."""

    type: Literal["usage.get"] = "usage.get"
    range: UsageRange = "30d"


class LiveUseExample(_Request):
    """Use an example track instead of Live (null goes back to Live). The hub answers with
    live.status, so every client sees the switch."""

    type: Literal["live.use_example"] = "live.use_example"
    name: str | None = Field(description="One of welcome.examples' names, or null.")


ClientMessage = Annotated[
    Union[Hello, ChatCreate, ChatSend, ChatUpdate, ChatOpen, ChatClose, TracksList,
          SettingsUpdate, SettingsTest, LiveUseExample, UsageGet, ChatSetMode, ActionConfirm,
          ActionSkip, ActionUndo, StepApply, VoiceTranscribe],
    Field(discriminator="type"),
]


# Hub -> client


class Welcome(_Message):
    type: Literal["welcome"] = "welcome"
    hub_version: str
    chats: list[ChatSummary]
    live: LiveStatus
    settings: Settings
    examples: list[TrackRef] = Field(
        default_factory=list, description="Example tracks to try while Live is offline."
    )
    voice: VoiceStatus = Field(default_factory=VoiceStatus)


class LiveStatusChanged(_Message):
    """Sent when Live connects or disconnects, or the selected track changes."""

    type: Literal["live.status"] = "live.status"
    live: LiveStatus


class Tracks(_Message):
    type: Literal["tracks"] = "tracks"
    request_id: str | None = None
    tracks: list[TrackRef]


class ChatChanged(_Message):
    type: Literal["chat.changed"] = "chat.changed"
    request_id: str | None = None
    chat: ChatSummary


class ChatClosed(_Message):
    type: Literal["chat.closed"] = "chat.closed"
    request_id: str | None = None
    chat_id: str


class ChatHistory(_Message):
    """A chat's turns, oldest first, in answer to chat.open."""

    type: Literal["chat.history"] = "chat.history"
    request_id: str | None = None
    chat_id: str
    turns: list[ChatTurn]


class ChatStatus(_Message):
    """What a running turn is doing before (or between) the parts the client can show.

    Sent as the turn starts and whenever the phase changes. Prose deltas mean the model is
    writing, and chat.done ends the turn.
    """

    type: Literal["chat.status"] = "chat.status"
    chat_id: str
    turn_id: str
    phase: Literal["reading", "thinking", "steps"] = Field(
        description="reading: the hub reads Live. thinking: the model works before it writes. "
        "steps: the model writes its steps."
    )
    detail: str | None = Field(
        default=None, description='What the phase is about, e.g. "Reading Kick, Sub Bass".'
    )


class ChatLooked(_Message):
    """Tracks the model read during a turn, sent as each read happens. Each message carries
    only the new reads; ChatTurn.looked_at has them all on replay."""

    type: Literal["chat.looked"] = "chat.looked"
    chat_id: str
    turn_id: str
    tracks: list[TrackRef]


class ChatDelta(_Message):
    """A piece of the assistant's prose, streamed as it is written."""

    type: Literal["chat.delta"] = "chat.delta"
    chat_id: str
    turn_id: str
    text: str


class ChatSteps(_Message):
    """The steps the model suggested. Optional: a prose-only answer sends none."""

    type: Literal["chat.steps"] = "chat.steps"
    chat_id: str
    turn_id: str
    steps: list[Step]
    listen_for: str | None = None
    caution: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ChatDone(_Message):
    type: Literal["chat.done"] = "chat.done"
    chat_id: str
    turn_id: str
    usage: Usage | None = Field(default=None, description="What the turn used, if it got that far.")


class ChatActions(_Message):
    """A turn's actions, all of them with their latest status. Sent when they are proposed
    and whenever any one's status changes."""

    type: Literal["chat.actions"] = "chat.actions"
    chat_id: str
    turn_id: str
    actions: list[Action]


class VoiceTranscript(_Message):
    """The text of a voice.transcribe, for the composer. Never sent as a message by itself."""

    type: Literal["voice.transcript"] = "voice.transcript"
    request_id: str
    text: str | None = None
    error: str | None = Field(default=None, description="Why it couldn't be transcribed.")


class UsageDay(_Shape):
    date: str = Field(description="Local date, YYYY-MM-DD.")
    usage: Usage


class UsageGroup(_Shape):
    key: str = Field(description="A model id, or a kind: chat, title, ask or eval.")
    usage: Usage


class UsageChat(_Shape):
    chat_id: str
    title: str | None = None
    open: bool = Field(description="False for a chat whose tab was closed.")
    usage: Usage


class UsageReport(_Message):
    """Usage for a range, in answer to usage.get. Days run oldest first, with no gaps."""

    type: Literal["usage.report"] = "usage.report"
    request_id: str | None = None
    range: UsageRange
    totals: dict[UsageRange, Usage] = Field(description="Today, 7 and 30 days, and all time.")
    days: list[UsageDay]
    by_model: list[UsageGroup]
    by_kind: list[UsageGroup]
    chats: list[UsageChat] = Field(description="Most expensive first.")
    cache_hit_rate: float | None = Field(
        default=None, description="Share of the prompt served from cache, 0 to 1."
    )
    prices_checked_on: str = Field(description="When the price table was last checked, YYYY-MM-DD.")


class UsageBudget(_Message):
    """This month's spend against the monthly budget. Sent after each turn while a budget is
    set."""

    type: Literal["usage.budget"] = "usage.budget"
    month_cost_usd: float = Field(description="Estimated spend since the 1st, local time.")
    limit_usd: float
    over: bool = Field(description="True once month_cost_usd passes limit_usd.")


class SettingsChanged(_Message):
    type: Literal["settings"] = "settings"
    request_id: str | None = None
    settings: Settings
    test_result: str | None = Field(default=None, description="Outcome of a settings.test.")


class ErrorMessage(_Message):
    type: Literal["error"] = "error"
    request_id: str | None = None
    code: Literal["bad_request", "unsupported_version", "not_found", "provider", "live", "internal"]
    message: str
    chat_id: str | None = Field(default=None, description="Set when a chat's turn failed.")
    turn_id: str | None = Field(
        default=None, description="The failed turn; chat.done for it follows."
    )


HubMessage = Annotated[
    Union[Welcome, LiveStatusChanged, Tracks, ChatChanged, ChatClosed, ChatHistory, ChatStatus,
          ChatDelta, ChatSteps, ChatDone, SettingsChanged, UsageReport, ErrorMessage,
          ChatLooked, UsageBudget, ChatActions, VoiceTranscript],
    Field(discriminator="type"),
]

class ClientEnvelope(RootModel[ClientMessage]):
    """Any message a client may send."""


class HubEnvelope(RootModel[HubMessage]):
    """Any message the hub may send."""


def parse_client(data: str | bytes) -> BaseModel:
    """Validate one message from a client. Raises pydantic.ValidationError."""
    return ClientEnvelope.model_validate_json(data).root


def parse_hub(data: str | bytes) -> BaseModel:
    """Validate one message from the hub (used by tests and Python clients)."""
    return HubEnvelope.model_validate_json(data).root


def _drop_field_titles(node):
    # Pydantic titles every field; json2ts would turn each into its own named type alias.
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for sub in props.values():
                sub.pop("title", None)
        for value in node.values():
            _drop_field_titles(value)
    elif isinstance(node, list):
        for value in node:
            _drop_field_titles(value)


def schema() -> dict:
    """JSON Schema for both directions, which the UI's TypeScript types are generated from.

    Client messages use the validation shape (defaults optional), hub messages the
    serialization shape (everything the hub always sends is required).
    """
    _, combined = models_json_schema(
        [(ClientEnvelope, "validation"), (HubEnvelope, "serialization")],
        ref_template="#/$defs/{model}",
    )
    # A shape used in both directions comes out as "X-Input" and "X-Output". Name the hub's
    # (always complete) shape plainly, so the UI reads ContextSet, and the sender's XInput.
    text = json.dumps(combined["$defs"])
    text = re.sub(r'([A-Za-z]+)-Output', r'\1', text)
    text = re.sub(r'([A-Za-z]+)-Input', r'\1Input', text)
    defs = json.loads(text)
    for name, d in defs.items():
        d["title"] = name  # json2ts names each type after its title
    _drop_field_titles(defs)
    for d in defs.values():
        # `type` has a default in Python, but every message on the wire carries it, and
        # TypeScript needs it required to narrow the unions.
        if "type" in d.get("properties", {}) and "type" not in d.get("required", []):
            d["required"] = ["type", *d.get("required", [])]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Vibe2knobProtocol",
        "description": f"vibe2knob hub protocol v{PROTOCOL_VERSION}",
        "type": "object",
        "properties": {
            "client": {"$ref": "#/$defs/ClientEnvelope"},
            "hub": {"$ref": "#/$defs/HubEnvelope"},
        },
        "$defs": dict(sorted(defs.items())),
    }


if __name__ == "__main__":
    print(json.dumps(schema(), indent=2))
