"""read_arrangement and read_scenes: who plays in a part of the song, and what's in a scene."""

from live_bridge import FakeLive
from vibe2knob.arrangement import Timeline, describe_arrangement, describe_range, find_locator
from vibe2knob.chat import MAX_READS, Chat, ChatEngine, TurnLooked, TurnStatus, gather_context
from vibe2knob.evals import Case, grade_reads
from vibe2knob.protocol import ContextSet
from vibe2knob.providers.fake import FakeProvider, Reply
from vibe2knob.session_view import Grid, describe_playing, describe_session_view, find_scene
from vibe2knob.tools import BUDGET_USED, ToolContext, default_tools

from .test_arrangement import real_set, simple
from .test_chat import run, session
from .test_session_view import grid, real_set as real_grid


def context(live=None, reads=MAX_READS):
    """A ToolContext over FakeLive's scripted song: six 16-bar sections by locator
    (Intro, Verse, Chorus, Break, Chorus 2, Outro), and the same song in Session view with
    the Chorus scene playing."""
    live = live or FakeLive()
    ctx = gather_context(ContextSet(follow_selection=True), live)
    return ToolContext([], live=live, summary=ctx.summary, reads_left=reads)


def call(name, payload, ctx):
    return default_tools().handle(name, payload, ctx).result


def arrangement(ctx, locator=None, first=None, last=None):
    return call("read_arrangement", {"from_bar": first, "to_bar": last, "locator": locator,
                                     "reason": "test"}, ctx)


# read_arrangement


def test_a_locator_by_name():
    ctx = context()
    text = arrangement(ctx, locator="break")
    assert text.startswith("Who plays in Break (bars 49-64): ")
    assert '"acoustic-pick": the whole range; clip "acoustic_native_fx"' in text
    assert '"Warm Pad": the whole range' in text
    assert "Lead Vox" not in text and "Drums" not in text  # not playing; groups left out
    assert ctx.reads_left == MAX_READS - 1


def test_locator_names_match_exactly_first_then_by_prefix():
    timeline = Timeline(context().summary.arrangement)
    assert find_locator(timeline, "Chorus")[0].first == 33  # exact beats the "Chorus 2" prefix
    assert find_locator(timeline, '"chorus 2"')[0].first == 65
    assert find_locator(timeline, "out")[0].name == "Outro"
    assert find_locator(timeline, "Drop") == (None, [])


def test_ambiguous_and_unknown_locators_dont_spend_a_read():
    timeline = Timeline(simple([[(1, 32)]], locators=[
        {"name": "Verse 1", "time": 0}, {"name": "Verse 2", "time": 64}]))
    assert find_locator(timeline, "verse") == (None, ["Verse 1", "Verse 2"])
    ctx = context()
    text = arrangement(ctx, locator="the drop")
    assert text.startswith('No locator is called "the drop". The locators are: "Intro", "Verse"')
    assert ctx.reads_left == MAX_READS


def test_a_range_of_bars_says_who_plays_and_where_it_changes():
    arr, names = real_set()
    text = describe_range(Timeline(arr), names, 67, 80)
    assert text.startswith("Who plays in bars 67-80: 14 of the 30 tracks with clips.")
    assert '"chorus chop": bars 70-76 (7 of 14); clip "hook [2022-05-13 174654]"' in text
    assert '- bar 70: "chorus chop" comes in; "chorus lead", "guitar licks 2" stop' in text
    assert "and 1 more change" in text


def test_bars_past_the_end_and_backwards():
    timeline = Timeline(simple([[(1, 8)], [(5, 16)]]))
    names = {0: "Vox", 1: "Pad"}
    assert describe_range(timeline, names, 20, 30) == (
        "The arrangement ends at bar 16, so nothing plays from bar 20.")
    text = describe_range(timeline, names, 12, 3)  # swapped, and clipped to the song
    assert text.startswith("Who plays in bars 3-12: 2 of the 2 tracks with clips.")
    assert '"Vox": bars 3-8 (6 of 10)' in text and '"Pad": bars 5-12 (8 of 10)' in text


def test_open_ends_and_missing_input():
    ctx = context()
    assert arrangement(ctx, first=81).startswith("Who plays in bars 81-96:")
    assert arrangement(ctx) == "Give from_bar and to_bar, or a locator name."


def test_without_locators_or_an_arrangement():
    live = FakeLive(arrangement={"signature": [4, 4], "tracks": [
        {"index": 0, "clips": [{"start": 0, "end": 32}]}]})
    assert arrangement(context(live), locator="Drop").startswith("The set has no locators.")
    assert arrangement(context(live), first=1, last=4).startswith("Who plays in bars 1-4:")
    ctx = context()
    ctx.summary.arrangement = None
    assert arrangement(ctx, first=1, last=8) == (
        "The Live link can't read the arrangement in this set.")


def test_the_read_budget_applies():
    ctx = context(reads=0)
    assert arrangement(ctx, locator="Intro") == BUDGET_USED
    assert call("read_scenes", {"scenes": ["Intro"], "reason": ""}, ctx) == BUDGET_USED


def test_big_sections_name_who_plays_throughout_and_count_the_rest():
    arr, names = real_set()
    text = describe_arrangement(Timeline(arr), names, [])
    assert '- bars 19-26: "chorus lead", "chorus backing", "brians bass", "80s bass", and 13 more' in text
    assert "- bars 67-80: 14 tracks, none throughout" in text
    assert '- bars 49-54: "guitar licks 2", "glitter synth" (part)' in text  # short: everyone
    assert "read_arrangement lists who plays in one" in text


# read_scenes


def scenes(ctx, *refs):
    return call("read_scenes", {"scenes": list(refs), "reason": "test"}, ctx)


def test_a_scene_by_name_lists_its_clips():
    ctx = context()
    text = scenes(ctx, "chorus")
    assert text.startswith('Scene 3 "Chorus" (playing now, all of it):')
    assert '- "Sub Bass": clip "sub_bass chorus" (16 bars), playing now' in text
    assert 'No clip in this scene: "acoustic-pick", "1-LABS (64 Bit)", "Warm Pad".' in text
    assert ctx.reads_left == MAX_READS - 1


def test_several_scenes_and_what_plays_now_are_one_read():
    ctx = context()
    text = scenes(ctx, "1", "scene 4", "playing")
    assert 'Scene 1 "Intro" (not playing now):' in text
    assert '"Warm Pad": clip "synth_pad intro" (16 bars), but it plays its clip in' not in text
    assert 'Scene 4 "Break" (not playing now):' in text
    assert 'Playing now from Session view: all of scene 3 "Chorus".' in text
    assert ctx.reads_left == MAX_READS - 1


def test_scene_refs():
    g = Grid(grid([[0], [1], [2]], names=["Verse 1", "Verse 2", "Drop"]))
    assert find_scene(g, "2") == (1, [])
    assert find_scene(g, "Scene 3") == (2, [])
    assert find_scene(g, "drop") == (2, [])
    assert find_scene(g, "verse") == (None, ['scene 1 "Verse 1"', 'scene 2 "Verse 2"'])
    assert find_scene(g, "9") == (None, []) and find_scene(g, "Outro") == (None, [])


def test_unknown_scenes_say_what_there_is():
    ctx = context()
    text = scenes(ctx, "the drop")
    assert text.startswith('No scene matches "the drop". There are 6, numbered from 1; '
                           'the named ones: scene 1 "Intro"')
    assert ctx.reads_left == MAX_READS  # nothing read, nothing spent


def test_playing_when_the_transport_is_stopped_and_on_a_real_set():
    view, names = real_grid()
    ctx = context(FakeLive(session_view=view.model_dump()))
    assert scenes(ctx, "playing") == "Nothing plays now: the transport is stopped."
    view, names = real_grid("probe_live11_playing.json")
    text = describe_playing(Grid(view), names)
    assert '- "bass lo": clip "riff 1" (2 bars) from scene 3' in text


def test_scene_lines_name_four_tracks():
    names = {0: "Kick", 1: "Bass", 2: "Pad", 3: "Vox", 4: "Gtr"}
    text = describe_session_view(Grid(grid([[0], [0], [0], [0], [0]])), names, [])
    assert '- scene 1: "Kick", "Bass", "Pad", "Vox", and 1 more' in text
    assert "read_scenes shows a scene's clips" in text


# In a turn


def test_a_range_read_in_a_turn():
    provider = FakeProvider([
        Reply("Let me look at the drop.", (("read_arrangement", {
            "from_bar": None, "to_bar": None, "locator": "Chorus", "reason": "who plays"}),
            ("read_scenes", {"scenes": ["playing"], "reason": "what they hear"}))),
        Reply("The bass and the vocal carry the chorus."),
    ])
    chat = Chat()
    events = run(ChatEngine(provider), chat, "what plays in the chorus?", FakeLive())
    statuses = [(e.phase, e.detail) for e in events if isinstance(e, TurnStatus)]
    assert ("reading", "Reading the arrangement at Chorus; what plays now") in statuses
    assert not any(isinstance(e, TurnLooked) for e in events)  # no track was read
    results = provider.calls[1]["messages"][-1]["tool_results"]
    assert results[0]["content"].startswith("Who plays in Chorus (bars 33-48):")
    assert results[1]["content"].startswith('Playing now from Session view: all of scene 3')
    assert chat.turns[-1].looked_at == []


def test_tools_without_live():
    provider = FakeProvider([
        Reply("", (("read_arrangement", {"from_bar": 1, "to_bar": 8, "locator": None,
                                          "reason": ""}),)),
        Reply("ok"),
    ])
    run(ChatEngine(provider), Chat(), "?", None)
    result = provider.calls[1]["messages"][-1]["tool_results"][0]["content"]
    assert result == "Live isn't connected, so there's no arrangement to read."


def test_session_without_a_session_view():
    ctx = context(session())
    assert scenes(ctx, "1") == "The Live link can't read Session view in this set."


def test_eval_grades_the_read_tools_called():
    case = Case(id="x", fixture="f", request="r", expect_tools=["read_arrangement"])
    assert all(c.passed for c in grade_reads(case, [], ["read_track", "read_arrangement"]))
    failed = [c for c in grade_reads(case, ["Beat"], ["read_scenes"]) if not c.passed]
    assert [c.name for c in failed] == ["calls the read tool the ask needs"]
