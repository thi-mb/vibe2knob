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


class ChatSummary(_Shape):
    """A chat as a tab shows it."""

    id: str
    title: str | None = Field(default=None, description="Topic title; null until named.")
    context: ContextSet
    updated_at: str = Field(description="ISO 8601 time of the last change.")


class LiveStatus(_Shape):
    connected: bool
    selected_track: TrackRef | None = None
    detail: str | None = Field(default=None, description="Why Live isn't reachable, if it isn't.")
    example: str | None = Field(
        default=None, description="Name of the example track in use instead of Live, if any."
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


class Settings(_Shape):
    """Settings as the client sees them. The API key itself never leaves the hub."""

    provider: Literal["claude", "ollama"] = "claude"
    model: str | None = None
    api_key_set: bool = False


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


class SettingsTest(_Request):
    type: Literal["settings.test"] = "settings.test"


class LiveUseExample(_Request):
    """Use an example track instead of Live (null goes back to Live). The hub answers with
    live.status, so every client sees the switch."""

    type: Literal["live.use_example"] = "live.use_example"
    name: str | None = Field(description="One of welcome.examples' names, or null.")


ClientMessage = Annotated[
    Union[Hello, ChatCreate, ChatSend, ChatUpdate, ChatOpen, ChatClose, TracksList,
          SettingsUpdate, SettingsTest, LiveUseExample],
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
    Union[Welcome, LiveStatusChanged, Tracks, ChatChanged, ChatClosed, ChatHistory, ChatDelta,
          ChatSteps, ChatDone, SettingsChanged, ErrorMessage],
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
