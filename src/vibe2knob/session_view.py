"""What sits in Session view's clip grid and what plays from it now, and the summary of it
the model reads.

Session view is a grid: tracks across, scenes down, a clip slot where they meet. A clip
playing from it is what the user hears right now, whatever the arrangement says. The hub
turns the grid into scenes (which tracks have clips in each) and "playing now", so the
model gets words instead of a grid to scan.

The block has a fixed budget (MAX_SCENES, MAX_PER_SCENE, MAX_CONTEXT, MAX_PLAYING), like
the arrangement block, so it stays about the same size on big sets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_SCENES = 12
MAX_PER_SCENE = 8
MAX_CONTEXT = 6
MAX_PLAYING = 8
MAX_SHARED = 4


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Slot(_Open):
    name: str = ""
    length: float | None = Field(None, description="The clip's length in beats.")


class SessionTrack(_Open):
    index: int
    is_group: bool = False
    slots: list[Slot | None] = Field(default_factory=list,
                                     description="One per scene; None for an empty slot.")
    playing_slot: int | None = Field(None, description="The scene whose clip plays now.")
    fired_slot: int | None = Field(None, description="A clip launched, waiting to start.")


class Scene(_Open):
    index: int
    name: str = ""


class SessionView(_Open):
    schema_version: Literal[1] = 1
    is_playing: bool | None = None
    scenes: list[Scene] = Field(default_factory=list)
    tracks: list[SessionTrack] = Field(default_factory=list)


def as_session_view(value: SessionView | dict | None) -> SessionView | None:
    if value is None or isinstance(value, SessionView):
        return value
    return SessionView.model_validate(value)


@dataclass
class SceneClips:
    index: int
    name: str
    tracks: list[int] = field(default_factory=list)

    def label(self) -> str:
        name = self.name.strip()
        return f'scene {self.index + 1} "{name}"' if name else f"scene {self.index + 1}"


class Grid:
    """Which tracks have clips in which scene, and what plays now, from a SessionView.

    Group tracks are left out of the scene lists: their slots only launch the clips of the
    tracks inside them.
    """

    def __init__(self, view: SessionView):
        self.view = view
        self.groups = {t.index for t in view.tracks if t.is_group}
        count = max([len(view.scenes)] + [len(t.slots) for t in view.tracks])
        names = {s.index: s.name for s in view.scenes}
        self.scenes = [SceneClips(i, names.get(i, "")) for i in range(count)]
        self._clips: dict[tuple[int, int], Slot] = {}
        for t in view.tracks:
            if t.index in self.groups:
                continue
            for s, slot in enumerate(t.slots):
                if slot is not None:
                    self._clips[(t.index, s)] = slot
                    self.scenes[s].tracks.append(t.index)

    @property
    def has_clips(self) -> bool:
        return bool(self._clips)

    def clip(self, track: int, scene: int) -> Slot | None:
        return self._clips.get((track, scene))

    def track_scenes(self, track: int) -> set[int]:
        return {s for (t, s) in self._clips if t == track}

    def playing(self) -> dict[int, int]:
        """Track -> the scene whose clip it plays now. Empty while the transport is stopped."""
        if self.view.is_playing is False:
            return {}
        return {t.index: t.playing_slot for t in self.view.tracks
                if t.index not in self.groups and t.playing_slot is not None
                and (t.index, t.playing_slot) in self._clips}

    def fired(self) -> dict[int, int]:
        return {t.index: t.fired_slot for t in self.view.tracks
                if t.index not in self.groups and t.fired_slot is not None
                and (t.index, t.fired_slot) in self._clips}

    def whole_scene(self) -> int | None:
        """The scene playing in full: every playing clip is from it and all its clips play."""
        playing = self.playing()
        scenes = set(playing.values())
        if len(scenes) != 1:
            return None
        scene = scenes.pop()
        return scene if set(self.scenes[scene].tracks) == set(playing) else None


def _list(names: dict[int, str], tracks: list[int], limit: int) -> str:
    shown = [f'"{names[t]}"' for t in tracks if t in names][:limit]
    known = sum(1 for t in tracks if t in names)
    if known > len(shown):
        shown.append(f"and {known - len(shown)} more")
    return ", ".join(shown)


def describe_session_view(grid: Grid, names: dict[int, str], context: list[int]) -> str | None:
    """The Session view part of the context block, or None when no slot holds a clip.

    `names` maps track index -> name for every track in the summary; `context` lists the
    tracks the chat is looking at (selected and pinned), which get the shared-scene lines.
    """
    if not grid.has_clips:
        return None
    used = [s for s in grid.scenes if any(t in names for t in s.tracks)]
    lines = [f"Session view ({len(grid.scenes)} scenes, {len(used)} with clips). Clips "
             "playing from it are what the user hears now:"]
    for s in used[:MAX_SCENES]:
        lines.append(f"  - {s.label()}: {_list(names, s.tracks, MAX_PER_SCENE)}")
    if len(used) > MAX_SCENES:
        lines.append(f"  - and {len(used) - MAX_SCENES} more scenes with clips")

    lines.append(f"  {_playing_text(grid, names)}")

    shown = [i for i in context if i in names and i not in grid.groups][:MAX_CONTEXT]
    pairs = [(a, b) for n, a in enumerate(shown) for b in shown[n + 1:]]
    if pairs:
        lines.append("  For the tracks in this chat:")
    for a, b in pairs:
        lines.append(f"  - {_pair_text(grid, names, a, b)}")
    return "\n".join(lines)


def _playing_text(grid: Grid, names: dict[int, str]) -> str:
    if grid.view.is_playing is False:
        return "Playing now: nothing, the transport is stopped."
    playing = {t: s for t, s in grid.playing().items() if t in names}
    fired = {t: s for t, s in grid.fired().items() if t in names}
    parts = []
    whole = grid.whole_scene()
    if whole is not None and playing:
        parts.append(f"Playing now: all of {grid.scenes[whole].label()}.")
    elif playing:
        clips = []
        for t, s in sorted(playing.items())[:MAX_PLAYING]:
            clip = grid.clip(t, s)
            clip_name = f' "{clip.name}"' if clip and clip.name.strip() else ""
            clips.append(f'"{names[t]}"{clip_name} ({grid.scenes[s].label()})')
        more = len(playing) - len(clips)
        parts.append("Playing now from Session view: " + ", ".join(clips)
                     + (f", and {more} more" if more > 0 else "") + ".")
    else:
        parts.append("Playing now: no Session view clips (tracks follow the arrangement).")
    if fired:
        parts.append("Launched, about to start: " + ", ".join(
            f'"{names[t]}" ({grid.scenes[s].label()})' for t, s in sorted(fired.items())[:MAX_PLAYING])
            + ".")
    return " ".join(parts)


def _pair_text(grid: Grid, names: dict[int, str], a: int, b: int) -> str:
    na, nb = f'"{names[a]}"', f'"{names[b]}"'
    mine, theirs = grid.track_scenes(a), grid.track_scenes(b)
    if not mine or not theirs:
        empty = [n for n, s in ((na, mine), (nb, theirs)) if not s]
        return f"{' and '.join(empty)} {'has' if len(empty) == 1 else 'have'} no Session view clips."
    both = sorted(mine & theirs)
    if not both:
        return f"{na} and {nb} never have clips in the same scene."
    shown = [grid.scenes[s] for s in both[:MAX_SHARED]]
    if any(s.name.strip() for s in shown):
        where = ", ".join(s.label() for s in shown)
    else:
        word = "scene" if len(both) == 1 else "scenes"
        where = f"{word} " + ", ".join(str(s.index + 1) for s in shown)
    if len(both) > MAX_SHARED:
        where += f" and {len(both) - MAX_SHARED} more"
    only = " only" if len(both) < max(len(mine), len(theirs)) else ""
    return f"{na} and {nb} have clips together{only} in {where}."
