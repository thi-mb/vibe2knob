import json

from live_bridge import FakeLive
from vibe2knob.arrangement import Arrangement
from vibe2knob.chat import gather_context
from vibe2knob.protocol import ContextSet
from vibe2knob.session import StaticLive, describe_summary
from vibe2knob.session_view import MAX_SCENES, Grid, SessionView, describe_session_view

from .conftest import FIXTURES
from .test_chat import load, session


def real_set():
    """Thiago's 7-track Session set, as LiveBridge.session_view() reads it."""
    probe = json.loads((FIXTURES / "session" / "probe_live11.json").read_text())
    tracks = []
    for r in probe["tracks"]:
        slots = [None if n is None else {"name": n, "length": length}
                 for n, length in zip(r["clips/name"], r["clips/length"])]
        tracks.append({"index": r["index"], "is_group": r["is_foldable"][0], "slots": slots})
    view = SessionView(is_playing=False, tracks=tracks,
                       scenes=[{"index": i, "name": n}
                               for i, n in enumerate(probe["scene_lists"]["scenes/name"])])
    return view, {r["index"]: r["name"][0] for r in probe["tracks"]}


def grid(rows, *, playing=None, fired=None, names=None, is_playing=True, groups=()):
    """`rows` is one list of scene indexes per track: where it has clips."""
    count = max([s + 1 for r in rows for s in r] + [1])
    names = names or []
    return SessionView(
        is_playing=is_playing,
        scenes=[{"index": i, "name": names[i] if i < len(names) else ""} for i in range(count)],
        tracks=[{"index": t, "is_group": t in groups,
                 "slots": [{"name": f"clip {t}.{s}"} if s in r else None for s in range(count)],
                 "playing_slot": (playing or {}).get(t), "fired_slot": (fired or {}).get(t)}
                for t, r in enumerate(rows)])


NAMES = {0: "Kick", 1: "Bass", 2: "Pad", 3: "Vox"}


def test_real_set_scenes_and_pairs():
    view, names = real_set()
    text = describe_session_view(Grid(view), names, [0, 3])
    assert "Session view (8 scenes, 5 with clips)" in text
    assert '- scene 1: "drum rack", "bass lo"' in text
    assert '- scene 4: "drum rack", "bass filthy", "6-64 Pads Dub Techno Kit", "chimes+delay"' in text
    assert '"bass"' not in text  # the group's slots are empty
    assert "the transport is stopped" in text
    assert '"drum rack" and "bass filthy" have clips together only in scenes 4, 5.' in text


def test_bass_parts_never_share_a_scene():
    view, names = real_set()
    text = describe_session_view(Grid(view), names, [2, 3])
    assert '"bass lo" and "bass filthy" never have clips in the same scene.' in text


def test_whole_scene_playing():
    view = grid([[0, 1], [1], [0]], playing={0: 1, 1: 1}, names=["Intro", "Drop"])
    text = describe_session_view(Grid(view), NAMES, [])
    assert 'Playing now: all of scene 2 "Drop".' in text


def test_clips_from_different_scenes_playing():
    view = grid([[0, 1], [1], [0]], playing={0: 0, 1: 1}, fired={2: 0})
    text = describe_session_view(Grid(view), NAMES, [])
    assert ('Playing now from Session view: "Kick" "clip 0.0" (scene 1), "Bass" "clip 1.1" '
            '(scene 2).') in text
    assert 'Launched, about to start: "Pad" (scene 1).' in text


def test_nothing_launched():
    text = describe_session_view(Grid(grid([[0]])), NAMES, [])
    assert "no Session view clips (tracks follow the arrangement)" in text


def test_stopped_transport_plays_nothing():
    view = grid([[0]], playing={0: 0}, is_playing=False)
    assert Grid(view).playing() == {}
    assert "the transport is stopped" in describe_session_view(Grid(view), NAMES, [])


def test_empty_grid_no_block():
    assert describe_session_view(Grid(grid([[], []])), NAMES, [0]) is None


def test_scene_budget():
    rows = [list(range(30))]
    text = describe_session_view(Grid(grid(rows)), NAMES, [])
    assert len([line for line in text.splitlines() if line.startswith("  - scene")]) == MAX_SCENES
    assert f"and {30 - MAX_SCENES} more scenes with clips" in text


def test_track_without_clips():
    text = describe_session_view(Grid(grid([[0], []])), NAMES, [0, 1])
    assert '"Bass" has no Session view clips.' in text


def test_summary_gets_a_session_block():
    states = [load("lead_vocal"), load("synth_pad")]  # indexes 0 and 2
    view = SessionView(scenes=[{"index": 0, "name": "A"}, {"index": 1, "name": "B"}],
                       tracks=[{"index": 0, "slots": [{"name": "vox"}, None]},
                               {"index": 2, "slots": [{"name": "pad"}, {"name": "pad"}]}])
    live = StaticLive(states, selected=0, session_view=view)
    ctx = gather_context(ContextSet(follow_selection=True, pinned=[2]), live)
    text = describe_summary(ctx.summary, {i: why for why, i, _ in ctx.detail})
    assert '- scene 1 "A": "Lead Vox", "Warm Pad"' in text
    assert '"Lead Vox" and "Warm Pad" have clips together only in scene 1 "A".' in text


def test_arrangement_only_set_has_no_session_block():
    states = [load("lead_vocal")]
    empty = SessionView(scenes=[{"index": 0}], tracks=[{"index": 0, "slots": [None]}])
    live = StaticLive(states, selected=0, session_view=empty,
                      arrangement=Arrangement(tracks=[{"index": 0, "clips": [
                          {"start": 0.0, "end": 64.0}]}]))
    ctx = gather_context(ContextSet(follow_selection=True), live)
    text = describe_summary(ctx.summary, {})
    assert "Arrangement" in text and "Session view" not in text


def test_no_session_view_no_block():
    ctx = gather_context(ContextSet(follow_selection=True), session())
    assert ctx.summary.session is None
    assert "Session view" not in describe_summary(ctx.summary, {})


def test_a_failing_session_read_doesnt_break_the_turn():
    class Broken(StaticLive):
        def session_view(self):
            raise RuntimeError("boom")

    ctx = gather_context(ContextSet(follow_selection=True), Broken([load("synth_pad")], selected=2))
    assert ctx.summary is not None and ctx.summary.session is None
    assert ctx.notes == []


def test_fake_live_plays_the_chorus():
    ctx = gather_context(ContextSet(follow_selection=True), FakeLive())
    text = describe_summary(ctx.summary, {0: "selected in Live"})
    assert 'Playing now: all of scene 3 "Chorus".' in text


def test_block_stays_small_on_a_big_set():
    def size(count):
        names = {t: f"Track number {t}" for t in range(count)}
        rows = [list(range(t % 5, 40, 2)) for t in range(count)]
        view = grid(rows, playing={t: rows[t][0] for t in range(count)})
        return len(describe_session_view(Grid(view), names, list(range(6))).encode())

    # A worst case (every track in every other scene, all playing, 6 tracks in the chat)
    # stays under 4 KB, and doesn't grow with the set.
    assert size(40) < 4000
    assert size(120) - size(40) < 100
