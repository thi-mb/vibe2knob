"""A stand-in for Live, built from track-state files such as the ones in fixtures/.

For tests, cloud development (no Live there) and the offline example tracks. Each file
becomes one track, in file-name order, re-indexed from 0.
"""

import copy
import json
from pathlib import Path

from .link import (ARRANGEMENT_SCHEMA_VERSION, CONNECTED, OFFLINE, SESSION_VIEW_SCHEMA_VERSION,
                   SUMMARY_SCHEMA_VERSION, LiveError, LiveLink)

# fixtures/ in a checkout; the wheel bundles them inside this package.
_HERE = Path(__file__).resolve().parent
FIXTURES = _HERE / "fixtures" if (_HERE / "fixtures").is_dir() else _HERE.parent / "fixtures"


# The scripted arrangement for the default fixtures: 16-bar sections, by locator.
_SECTIONS = ["Intro", "Verse", "Chorus", "Break", "Chorus 2", "Outro"]
# Which sections each fixture track plays in, by file stem. Unknown tracks play throughout.
# The Drums group holds no clips; Beat, right below it, plays inside it.
_GROUPS = {"drum_group"}
_GROUPED = {"drum_rack"}
_PLAYS = {
    "acoustic_native_fx": ["Verse", "Break"],
    "drum_rack": ["Verse", "Chorus", "Chorus 2"],
    "guitar_plugins": ["Chorus", "Chorus 2"],
    "labs_plugin_instrument": ["Intro", "Break", "Outro"],
    "lead_vocal": ["Verse", "Chorus", "Chorus 2"],
    "sub_bass": ["Chorus", "Chorus 2"],
    "synth_pad": ["Intro", "Break", "Outro"],
}


def _scripted_arrangement(stems):
    bar = 4.0
    section = {name: i * 16 * bar for i, name in enumerate(_SECTIONS)}
    tracks = []
    for i, stem in enumerate(stems):
        clips = None
        if stem not in _GROUPS:
            clips = [{"name": stem, "start": section[n], "end": section[n] + 16 * bar}
                     for n in _PLAYS.get(stem, _SECTIONS)]
        tracks.append({"index": i, "is_group": stem in _GROUPS, "is_grouped": stem in _GROUPED,
                       "clips": clips})
    return {
        "schema_version": ARRANGEMENT_SCHEMA_VERSION,
        "song_length": len(_SECTIONS) * 16 * bar,
        "signature": [4, 4],
        "locators": [{"name": n, "time": t} for n, t in section.items()],
        "tracks": tracks,
    }


def _scripted_session_view(stems, playing="Chorus"):
    """The same song as a Session view grid: one scene per section, `playing` launched."""
    scene = _SECTIONS.index(playing) if playing else None
    tracks = []
    for i, stem in enumerate(stems):
        group = stem in _GROUPS
        plays = set() if group else set(_PLAYS.get(stem, _SECTIONS))
        slots = [{"name": f"{stem} {n.lower()}", "length": 64.0} if n in plays else None
                 for n in _SECTIONS]
        tracks.append({"index": i, "is_group": group, "slots": slots,
                       "playing_slot": scene if scene is not None and slots[scene] else None,
                       "fired_slot": None})
    return {
        "schema_version": SESSION_VIEW_SCHEMA_VERSION,
        "is_playing": scene is not None,
        "scenes": [{"index": i, "name": n} for i, n in enumerate(_SECTIONS)],
        "tracks": tracks,
    }


class FakeLive(LiveLink):
    def __init__(self, tracks=None, *, selected=0, tempo=None, live_version="11.3",
                 connected=True, arrangement=None, session_view=None):
        """`tracks` is a list of track-state dicts or paths, or a directory of JSON files.

        Defaults to fixtures/, with a scripted arrangement and the same song in Session
        view with the Chorus scene playing. `arrangement` and `session_view` are what
        arrangement() and session_view() return (see link.py); without them, other tracks
        have none.
        """
        super().__init__()
        if tracks is None:
            stems = [f.stem for f in sorted(FIXTURES.glob("*.json"))]
            if arrangement is None:
                arrangement = _scripted_arrangement(stems)
            if session_view is None:
                session_view = _scripted_session_view(stems)
        self._arrangement = arrangement
        self._session_view = session_view
        if tracks is None or isinstance(tracks, (str, Path)):
            folder = Path(tracks) if tracks is not None else FIXTURES
            tracks = sorted(folder.glob("*.json"))
        self._states = []
        for i, item in enumerate(tracks):
            state = json.loads(Path(item).read_text()) if isinstance(item, (str, Path)) else item
            state = copy.deepcopy(state)
            state["track"]["index"] = i
            self._states.append(state)
        if tempo is None:
            tempo = next((s["tempo"] for s in self._states if s.get("tempo")), 120.0)
        self.tempo = tempo
        self.live_version = live_version
        self._selected = selected if self._states else None
        self._started = False
        if connected:
            self._set_status(CONNECTED)

    # Test controls

    def select(self, index):
        """Move Live's selection, as clicking a track would. None selects a return/master."""
        if index is not None and not 0 <= index < len(self._states):
            raise IndexError(index)
        self._set_selected(index)

    def set_connected(self, connected):
        """Simulate Live quitting or coming back."""
        if connected:
            self._set_status(CONNECTED)
        else:
            self._set_selected(None)
            self._set_status(OFFLINE, "Live isn't running (fake).")

    # LiveLink

    def start(self):
        self._started = True

    def _check(self):
        if self.status != CONNECTED:
            raise LiveError(self.detail or "Live isn't running (fake).")

    def ping(self):
        self._check()

    def selected_track_index(self):
        self._check()
        return self._selected

    def track_state(self, track_index):
        self._check()
        if not 0 <= track_index < len(self._states):
            raise LiveError(f"No track {track_index}.")
        state = copy.deepcopy(self._states[track_index])
        state.setdefault("tempo", self.tempo)
        return state

    def session_summary(self):
        self._check()
        tracks = []
        for state in self._states:
            track = state["track"]
            mixer = track.get("mixer", {})
            tracks.append({
                "index": track["index"],
                "name": track["name"],
                "type": track["type"],
                "color": track.get("color"),
                "devices": [d["name"] for d in track.get("devices", [])],
                "volume": mixer.get("volume"),
                "panning": mixer.get("panning"),
                "mute": bool(mixer.get("mute", False)),
            })
        return {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "live_version": self.live_version,
            "tempo": self.tempo,
            "tracks": tracks,
        }

    def arrangement(self):
        self._check()
        return copy.deepcopy(self._arrangement)

    def session_view(self):
        self._check()
        return copy.deepcopy(self._session_view)
