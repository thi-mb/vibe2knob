import json

from live_bridge import FakeLive
from live_bridge.client import _clip_spans
from vibe2knob.arrangement import MAX_SECTIONS, Arrangement, Timeline, describe_arrangement
from vibe2knob.chat import gather_context
from vibe2knob.protocol import ContextSet
from vibe2knob.session import StaticLive, describe_summary

from .conftest import FIXTURES
from .test_chat import load, session


def real_set():
    """Thiago's 39-track set, as LiveBridge.arrangement() reads it (see test_live_bridge)."""
    probe = json.loads((FIXTURES / "arrangement" / "probe_live11.json").read_text())
    tracks = []
    for r in probe["tracks"]:
        group = r["is_foldable"][0]
        clips = None if group else _clip_spans(
            r["arrangement_clips/name"], r["arrangement_clips/start_time"],
            r["arrangement_clips/length"])
        tracks.append({"index": r["index"], "is_group": group, "is_grouped": r["is_grouped"][0],
                       "clips": clips})
    names = {r["index"]: r["name"][0] for r in probe["tracks"]}
    return Arrangement(song_length=504.0, signature=[4, 4], tracks=tracks), names


def bars(*ranges, bpb=4.0):
    return [{"start": (a - 1) * bpb, "end": b * bpb} for a, b in ranges]


def simple(plays, locators=(), groups=()):
    return Arrangement(signature=[4, 4], locators=list(locators), tracks=[
        {"index": i, "is_group": i in groups, "is_grouped": False,
         "clips": None if i in groups else bars(*r)}
        for i, r in enumerate(plays)
    ])


def test_trumpet_and_chorus_chop_never_meet():
    arrangement, names = real_set()
    text = describe_arrangement(Timeline(arrangement), names, [19, 1])
    assert '"trumpet" and "chorus chop" never play at the same time' in text
    assert "the set has no locators" in text
    sections = [line for line in text.splitlines() if line.startswith("  - bar")]
    assert 1 < len(sections) <= MAX_SECTIONS
    assert len(text.encode()) < 3500  # the budget keeps a 39-track set small


def test_groups_play_where_their_tracks_do():
    arrangement, _ = real_set()
    timeline = Timeline(arrangement)
    assert timeline.bars(23) >= timeline.bars(28)  # drums > kick
    assert timeline.bars(23) >= timeline.bars(26)  # drums > briandrums (nested)
    assert timeline.related(23, 28) and not timeline.related(0, 28)


def test_overlap_in_words():
    timeline = Timeline(simple([[(1, 32)], [(17, 48)], [(33, 40)]]))
    names = {0: "Vox", 1: "Pad", 2: "Lead"}
    text = describe_arrangement(timeline, names, [0, 1, 2])
    assert '"Vox" and "Pad" play together in bars 17-32 (16 of "Vox"\'s 32 bars' in text
    assert '"Vox" and "Lead" never play at the same time' in text
    assert '"Pad" and "Lead" play together in bars 33-40' in text


def test_locators_name_the_sections():
    locators = [{"name": "Verse", "time": 16.0}, {"name": "Chorus", "time": 48.0}]
    timeline = Timeline(simple([[(1, 20)], [(13, 24)]], locators))
    sections, from_locators = timeline.sections()
    assert from_locators
    assert [s.label() for s in sections] == [
        "Before the first locator (bars 1-4)", "Verse (bars 5-12)", "Chorus (bars 13-24)"]


def test_many_locators_fit_the_budget():
    locators = [{"name": f"L{i}", "time": i * 8.0} for i in range(30)]
    timeline = Timeline(simple([[(1, 60)]], locators))
    sections, _ = timeline.sections()
    assert len(sections) == MAX_SECTIONS
    assert sections[0].first == 1 and sections[-1].last == 60


def test_a_tail_over_the_bar_line_doesnt_count():
    arrangement = Arrangement(tracks=[{"index": 0, "clips": [{"start": 0.0, "end": 16.25}]}])
    assert Timeline(arrangement).bars(0) == {1, 2, 3, 4}


def test_no_clips_says_so():
    names = {0: "Vox"}
    text = describe_arrangement(Timeline(simple([[]])), names, [0])
    assert "no clips are placed in Arrangement view" in text


def test_summary_lines_say_where_tracks_play():
    states = [load("lead_vocal"), load("synth_pad")]  # indexes 0 and 2
    arrangement = Arrangement(tracks=[{"index": 0, "clips": bars((1, 16))},
                                      {"index": 2, "clips": bars((17, 32))}])
    live = StaticLive(states, selected=0, arrangement=arrangement)
    ctx = gather_context(ContextSet(follow_selection=True, pinned=[2]), live)
    text = describe_summary(ctx.summary, {i: why for why, i, _ in ctx.detail})
    assert '"Lead Vox" (audio): EQ Eight, Compressor, Reverb [plays bars 1-16; selected' in text
    assert '"Lead Vox" and "Warm Pad" never play at the same time' in text


def test_no_arrangement_no_block():
    ctx = gather_context(ContextSet(follow_selection=True), session())
    assert ctx.summary.arrangement is None
    assert "Arrangement" not in describe_summary(ctx.summary, {})


def test_a_failing_arrangement_read_doesnt_break_the_turn():
    class Broken(StaticLive):
        def arrangement(self):
            raise RuntimeError("boom")

    ctx = gather_context(ContextSet(follow_selection=True), Broken([load("synth_pad")], selected=2))
    assert ctx.summary is not None and ctx.summary.arrangement is None
    assert ctx.notes == []


def test_fake_live_has_an_arrangement():
    ctx = gather_context(ContextSet(follow_selection=True), FakeLive())
    text = describe_summary(ctx.summary, {0: "selected in Live"})
    assert "Sections, from the set's locators (" in text
    assert "Intro (bars 1-16)" in text
