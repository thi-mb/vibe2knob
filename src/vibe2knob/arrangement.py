"""When tracks play: the arrangement map from Live, and the summary of it the model reads.

The hub does the interval arithmetic, so the model gets answers in words ("never play at
the same time") instead of dozens of ranges to compare. Everything is in bars, numbered
from 1 as Live shows them. A track "plays" in a bar when it has clips there, which doesn't
prove it's audible: a clip can be muted, silent or automated down, and AbletonOSC can't see
any of that.

The block has a fixed budget (MAX_SECTIONS, MAX_RANGES, MAX_CONTEXT, MAX_ALONGSIDE), so it
stays about the same size on long songs and big sets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_SECTIONS = 12
# Shorter auto sections are folded into a neighbour before the budget is applied.
MIN_SECTION_BARS = 2
MAX_RANGES = 6
MAX_CONTEXT = 6
MAX_ALONGSIDE = 6
MAX_PER_SECTION = 10
# Less than this many beats of clip in a bar (a tail spilling over the bar line) doesn't count.
MIN_BEATS_IN_BAR = 0.5


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Clip(_Open):
    name: str = ""
    start: float = Field(description="Beats from the song's start.")
    end: float


class ArrangementTrack(_Open):
    index: int
    is_group: bool = False
    is_grouped: bool = False
    clips: list[Clip] | None = Field(None, description="None for groups, or when unread.")


class Locator(_Open):
    name: str = ""
    time: float


class Arrangement(_Open):
    schema_version: Literal[1] = 1
    song_length: float | None = None
    signature: list[int] | None = None
    locators: list[Locator] = Field(default_factory=list)
    tracks: list[ArrangementTrack] = Field(default_factory=list)

    @property
    def beats_per_bar(self) -> float:
        if self.signature and len(self.signature) == 2 and all(self.signature):
            numerator, denominator = self.signature
            return numerator * 4 / denominator
        return 4.0


def as_arrangement(value: Arrangement | dict | None) -> Arrangement | None:
    if value is None or isinstance(value, Arrangement):
        return value
    return Arrangement.model_validate(value)


@dataclass
class Section:
    first: int
    last: int
    name: str | None = None

    @property
    def bars(self) -> int:
        return self.last - self.first + 1

    def label(self) -> str:
        where = f"bars {self.first}-{self.last}" if self.bars > 1 else f"bar {self.first}"
        return f"{self.name} ({where})" if self.name else where


def bar_ranges(bars, limit: int | None = None) -> str:
    """'bars 1-16, 33-48' from a collection of bar numbers."""
    runs = _runs(sorted(set(bars)))
    if not runs:
        return "no bars"
    shown = runs if limit is None else runs[:limit]
    text = ", ".join(f"{a}-{b}" if a != b else f"{a}" for a, b in shown)
    word = "bar" if len(runs) == 1 and runs[0][0] == runs[0][1] else "bars"
    if len(shown) < len(runs):
        more = len(runs) - len(shown)
        text += f" and {more} more stretch{'es' if more > 1 else ''}"
    return f"{word} {text}"


def _runs(bars: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for b in bars:
        if runs and b == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], b)
        else:
            runs.append((b, b))
    return runs


class Timeline:
    """Which bars each track plays in, worked out from an Arrangement."""

    def __init__(self, arrangement: Arrangement):
        self.arrangement = arrangement
        self.bpb = arrangement.beats_per_bar
        self.groups = {t.index for t in arrangement.tracks if t.is_group}
        self._bars: dict[int, set[int]] = {}
        for t in arrangement.tracks:
            if not t.is_group and t.clips is not None:
                self._bars[t.index] = self._clip_bars(t.clips)
        self._members = self._group_members()
        for g, members in self._members.items():
            self._bars[g] = set().union(*(self._bars.get(m, set()) for m in members))

    def _clip_bars(self, clips: list[Clip]) -> set[int]:
        covered: dict[int, float] = {}
        for c in clips:
            bar = math.floor(c.start / self.bpb)
            while bar * self.bpb < c.end:
                lo, hi = max(c.start, bar * self.bpb), min(c.end, (bar + 1) * self.bpb)
                covered[bar + 1] = covered.get(bar + 1, 0.0) + max(0.0, hi - lo)
                bar += 1
        return {b for b, beats in covered.items() if beats >= min(MIN_BEATS_IN_BAR, self.bpb / 2)}

    def _group_members(self) -> dict[int, set[int]]:
        """Each group's tracks, nested groups included.

        AbletonOSC says whether a track is in a group but not which one, so a grouped track
        goes to the nearest group above it. Tracks after a nested group's last child land
        in that nested group too, which can make it look busier than it is.
        """
        members: dict[int, set[int]] = {g: set() for g in self.groups}
        chain: list[int] = []
        for t in sorted(self.arrangement.tracks, key=lambda t: t.index):
            if not t.is_grouped:
                chain = []
            for g in chain:
                members[g].add(t.index)
            if t.is_group:
                chain = chain + [t.index]
        return members

    @property
    def has_clips(self) -> bool:
        return any(self._bars.get(i) for i in self._bars if i not in self.groups)

    def knows(self, index: int) -> bool:
        return index in self._bars

    def bars(self, index: int) -> set[int]:
        return self._bars.get(index, set())

    def related(self, a: int, b: int) -> bool:
        """A group and a track inside it trivially play together, so don't compare them."""
        return b in self._members.get(a, ()) or a in self._members.get(b, ())

    @property
    def last_bar(self) -> int:
        """The last bar any clip reaches. Live's song_length can run past it."""
        return max((max(b) for b in self._bars.values() if b), default=0)

    def sections(self) -> tuple[list[Section], bool]:
        """Sections within the budget, and whether they come from the set's locators."""
        last = self.last_bar
        if last == 0:
            return [], False
        locators = sorted(self.arrangement.locators, key=lambda c: c.time)
        if locators:
            sections = self._locator_sections(locators, last)
            from_locators = True
        else:
            sections = self._auto_sections(last)
            from_locators = False
            sections = self._merge(sections, min_bars=MIN_SECTION_BARS)
        return self._merge(sections, max_count=MAX_SECTIONS), from_locators

    def _locator_sections(self, locators: list[Locator], last: int) -> list[Section]:
        starts: list[tuple[int, str]] = []
        for c in locators:
            bar = round(c.time / self.bpb) + 1
            if bar > last:
                continue
            name = c.name.strip() or "Unnamed locator"
            if starts and starts[-1][0] == bar:
                starts[-1] = (bar, name)
            else:
                starts.append((bar, name))
        if not starts or starts[0][0] > 1:
            starts.insert(0, (1, "Before the first locator"))
        sections = []
        for i, (bar, name) in enumerate(starts):
            end = starts[i + 1][0] - 1 if i + 1 < len(starts) else last
            sections.append(Section(bar, end, name))
        return sections

    def _players(self, first: int, last: int) -> set[int]:
        out = set()
        for i, bars in self._bars.items():
            if i not in self.groups and any(first <= b <= last for b in bars):
                out.add(i)
        return out

    def _auto_sections(self, last: int) -> list[Section]:
        sections: list[Section] = []
        previous = None
        for bar in range(1, last + 1):
            players = frozenset(i for i, b in self._bars.items()
                                if i not in self.groups and bar in b)
            if sections and players == previous:
                sections[-1].last = bar
            else:
                sections.append(Section(bar, bar))
            previous = players
        return sections

    def _merge(self, sections: list[Section], *, min_bars: int = 0,
               max_count: int | None = None) -> list[Section]:
        """Fold the shortest section into its most similar neighbour, again and again,
        while one is shorter than `min_bars` or there are more than `max_count`."""
        sections = list(sections)
        while len(sections) > 1:
            if max_count is not None and len(sections) > max_count:
                flagged = range(len(sections))
            else:
                flagged = [i for i, s in enumerate(sections) if s.bars < min_bars]
            if not flagged:
                break
            i = min(flagged, key=lambda i: (sections[i].bars, i))
            neighbours = [j for j in (i - 1, i + 1) if 0 <= j < len(sections)]
            mine = self._players(sections[i].first, sections[i].last)

            def likeness(j):
                theirs = self._players(sections[j].first, sections[j].last)
                union = mine | theirs
                return (len(mine & theirs) / len(union) if union else 1.0, -sections[j].bars)

            j = max(neighbours, key=likeness)
            a, b = sorted((i, j))
            names = [n for n in (sections[a].name, sections[b].name) if n]
            sections[a:b + 1] = [Section(sections[a].first, sections[b].last,
                                         " + ".join(names) if names else None)]
        return sections


def describe_arrangement(timeline: Timeline, names: dict[int, str], context: list[int]) -> str:
    """The Arrangement part of the context block.

    `names` maps track index -> name for every track in the summary; `context` lists the
    tracks the chat is looking at (selected and pinned), which get the overlap lines.
    """
    arrangement = timeline.arrangement
    if not timeline.has_clips:
        return ("Arrangement: no clips are placed in Arrangement view, so you can't tell which "
                "tracks play together (they may be working in Session view).")
    sig = arrangement.signature
    head = [f"{sig[0]}/{sig[1]}" if sig else None, f"{timeline.last_bar} bars"]
    lines = [f"Arrangement ({', '.join(h for h in head if h)}). A track plays where it has "
             "clips, which may still be muted or silent:"]
    sections, from_locators = timeline.sections()
    lines.append("  Sections, from the set's locators:" if from_locators
                 else "  Sections, from where tracks start and stop (the set has no locators):")
    for s in sections:
        lines.append(f"  - {s.label()}: {_players_text(timeline, names, s)}")

    shown = [i for i in context if timeline.knows(i) and i in names][:MAX_CONTEXT]
    if shown:
        lines.append("  For the tracks in this chat:")
    for n, a in enumerate(shown):
        for b in shown[n + 1:]:
            if not timeline.related(a, b):
                lines.append(f"  - {_pair_text(timeline, names, a, b)}")
    for a in shown:
        mine = timeline.bars(a)
        if not mine:
            lines.append(f'  - "{names[a]}" has no clips in the arrangement.')
            continue
        shared = []
        for b, bars in timeline._bars.items():
            if b == a or b in timeline.groups or b not in names or timeline.related(a, b):
                continue
            overlap = len(mine & bars)
            if overlap:
                shared.append((overlap, b))
        shared.sort(key=lambda x: (-x[0], x[1]))
        if shared:
            top = ", ".join(f'"{names[b]}" ({o} bars)' for o, b in shared[:MAX_ALONGSIDE])
            more = f", and {len(shared) - MAX_ALONGSIDE} more" if len(shared) > MAX_ALONGSIDE else ""
            lines.append(f'  - "{names[a]}" ({len(mine)} bars) shares the most bars with {top}{more}.')
        else:
            lines.append(f'  - "{names[a]}" never plays at the same time as any other track.')
    return "\n".join(lines)


def _players_text(timeline: Timeline, names: dict[int, str], s: Section) -> str:
    """Who plays in a section: whole-section players first, then the busiest of the rest."""
    players = []
    for i in timeline._players(s.first, s.last):
        if i in names:
            inside = sum(1 for b in timeline.bars(i) if s.first <= b <= s.last)
            players.append((-inside, i))
    if not players:
        return "nothing plays"
    shown = sorted(sorted(players)[:MAX_PER_SECTION], key=lambda p: (p[0] != -s.bars, p[1]))
    parts = [f'"{names[i]}"' + ("" if -n == s.bars else " (part)") for n, i in shown]
    if len(players) > len(shown):
        parts.append(f"and {len(players) - len(shown)} more in part")
    return ", ".join(parts)


def _pair_text(timeline: Timeline, names: dict[int, str], a: int, b: int) -> str:
    mine, theirs = timeline.bars(a), timeline.bars(b)
    na, nb = f'"{names[a]}"', f'"{names[b]}"'
    if not mine or not theirs:
        return f"{na} and {nb} never play at the same time."
    both = mine & theirs
    if not both:
        return (f"{na} and {nb} never play at the same time ({na}: {bar_ranges(mine, MAX_RANGES)}; "
                f"{nb}: {bar_ranges(theirs, MAX_RANGES)}).")
    return (f"{na} and {nb} play together in {bar_ranges(both, MAX_RANGES)} "
            f"({len(both)} of {na}'s {len(mine)} bars, {len(both)} of {nb}'s {len(theirs)}).")


def plays_note(timeline: Timeline, index: int) -> str | None:
    """The note on a summary line: where this track plays."""
    if not timeline.knows(index):
        return None
    bars = timeline.bars(index)
    if not bars:
        return "no clips in the arrangement"
    return "plays " + bar_ranges(bars, MAX_RANGES)
