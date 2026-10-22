"""The light whole-session summary, and what the chat engine reads from Live.

The Live link (live_bridge) returns plain dicts so it doesn't import vibe2knob; the
models here validate them. Unknown fields are kept, like in state.py.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .arrangement import Arrangement, Timeline, as_arrangement, describe_arrangement, plays_note
from .state import Device, Track, TrackState, TrackType


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class TrackSummary(_Open):
    index: int
    name: str
    type: TrackType
    color: int | None = None
    devices: list[str] = Field(default_factory=list, description="Top-level device names.")
    volume: float | None = None
    panning: float | None = None
    mute: bool | None = None

    def as_state(self) -> TrackState:
        """A device-names-only TrackState, so steps on a summary-only track can be checked."""
        return TrackState(
            track=Track(
                index=self.index,
                name=self.name,
                type=self.type,
                devices=[Device(name=n) for n in self.devices],
            )
        )


class SessionSummary(_Open):
    schema_version: Literal[1] = 1
    live_version: str | None = None
    tempo: float | None = None
    tracks: list[TrackSummary] = Field(default_factory=list)
    arrangement: Arrangement | None = Field(
        None, description="Where clips sit in Arrangement view, when the Live link can tell."
    )

    def track(self, index: int) -> TrackSummary | None:
        return next((t for t in self.tracks if t.index == index), None)


class LiveView(Protocol):
    """What the chat engine needs from Live. LiveBridge and FakeLive both fit."""

    def session_summary(self) -> SessionSummary | dict: ...

    def selected_track_index(self) -> int | None: ...

    def track_state(self, index: int) -> TrackState | dict: ...

    # Optional: arrangement() -> Arrangement | dict | None (see live_bridge/link.py).


def read_arrangement(live: LiveView) -> Arrangement | None:
    """The arrangement map, or None when this LiveView has none or can't read it."""
    read = getattr(live, "arrangement", None)
    return as_arrangement(read()) if read else None


def as_summary(value: SessionSummary | dict) -> SessionSummary:
    return value if isinstance(value, SessionSummary) else SessionSummary.model_validate(value)


def as_track_state(value: TrackState | dict) -> TrackState:
    return value if isinstance(value, TrackState) else TrackState.model_validate(value)


def summarize(states: list[TrackState]) -> SessionSummary:
    """A session summary built from full track states (fixtures, tests, evals)."""
    tracks = []
    for i, s in enumerate(states):
        t = s.track
        mixer = t.mixer
        tracks.append(
            TrackSummary(
                index=t.index if t.index is not None else i,
                name=t.name,
                type=t.type,
                color=t.color,
                devices=[d.name for d in t.devices],
                volume=mixer.volume if mixer else None,
                panning=mixer.panning if mixer else None,
                mute=mixer.mute if mixer else None,
            )
        )
    first = states[0] if states else None
    return SessionSummary(
        live_version=first.live_version if first else None,
        tempo=first.tempo if first else None,
        tracks=tracks,
    )


class StaticLive:
    """A LiveView over fixed track states, for tests and evals."""

    def __init__(self, states: list[TrackState], selected: int | None = None,
                 arrangement: Arrangement | dict | None = None):
        self._arrangement = as_arrangement(arrangement)
        self.states: dict[int, TrackState] = {}
        for i, s in enumerate(states):
            self.states[s.track.index if s.track.index is not None else i] = s
        self.selected = selected

    def session_summary(self) -> SessionSummary:
        return summarize(list(self.states.values()))

    def selected_track_index(self) -> int | None:
        return self.selected

    def track_state(self, index: int) -> TrackState:
        return self.states[index]

    def arrangement(self) -> Arrangement | None:
        return self._arrangement


def describe_summary(summary: SessionSummary, detail: dict[int, str]) -> str:
    """The summary block of the prompt. `detail` maps track index -> why it has full detail.

    With an arrangement, each line also says where the track plays, and an Arrangement
    part follows with the sections and how the tracks in `detail` overlap.
    """
    timeline = Timeline(summary.arrangement) if summary.arrangement else None
    head = []
    if summary.live_version:
        head.append(f"Live {summary.live_version}")
    if summary.tempo:
        head.append(f"{summary.tempo:g} BPM")
    lines = [("Session: " + ", ".join(head) + ".") if head else "Session:"]
    if not summary.tracks:
        lines.append("  (no tracks)")
    for t in summary.tracks:
        devices = ", ".join(t.devices) if t.devices else "no devices"
        line = f'  - "{t.name}" ({t.type}): {devices}'
        notes = []
        plays = plays_note(timeline, t.index) if timeline and timeline.has_clips else None
        if plays:
            notes.append(plays)
        if t.mute:
            notes.append("muted")
        if t.index in detail:
            notes.append(f"{detail[t.index]}, full detail below")
        if notes:
            line += f" [{'; '.join(notes)}]"
        lines.append(line)
    if timeline and summary.tracks:
        names = {t.index: t.name for t in summary.tracks}
        lines.append(describe_arrangement(timeline, names, list(detail)))
    return "\n".join(lines)

