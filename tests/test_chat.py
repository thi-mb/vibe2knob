import copy

import pytest

from vibe2knob.chat import (
    AssistantTurn,
    Chat,
    ChatEngine,
    ChatTitled,
    TurnDone,
    TurnError,
    TurnStarted,
    TurnStatus,
    TurnSteps,
    TurnText,
    diff_tracks,
)
from vibe2knob.protocol import ContextSet
from vibe2knob.providers.fake import FakeProvider, Reply
from vibe2knob.session import StaticLive
from vibe2knob.state import Device, Parameter, TrackState

from .conftest import FIXTURES, WIDER_PAD


def load(name):
    return TrackState.load(FIXTURES / f"{name}.json")


def session(selected=2):
    # Indexes from the fixtures: vocal 0, bass 1, pad 2, drums 4.
    return StaticLive([load("lead_vocal"), load("sub_bass"), load("synth_pad"), load("drum_group")],
                      selected=selected)


def wider_reply():
    return Reply.with_steps(
        "The pad is already stereo, so open the Utility and lean on the chorus.",
        copy.deepcopy(WIDER_PAD["steps"]),
        WIDER_PAD["listen_for"],
        WIDER_PAD["caution"],
    )


def run(engine, chat, text, live):
    return list(engine.send(chat, text, live))


def test_prose_only_answer():
    provider = FakeProvider([Reply("Echo is the newer delay, with modulation and a tape feel.")])
    engine = ChatEngine(provider)
    chat = Chat(context=ContextSet(follow_selection=False))
    events = run(engine, chat, "what's the difference between Echo and Delay?", None)
    assert isinstance(events[0], TurnStarted)
    assert "".join(e.text for e in events if isinstance(e, TurnText)).startswith("Echo is")
    assert not any(isinstance(e, TurnSteps) for e in events)
    assert isinstance(events[-2], TurnDone) and events[-1].title == "Scripted chat"
    assert chat.title == "Scripted chat"
    user, answer = chat.turns
    assert user.text.startswith("what's") and answer.steps == [] and answer.error is None
    sent = provider.calls[0]["messages"][-1]["content"]
    assert "No tracks are in this chat's context." in sent
    assert [t.name for t in provider.calls[0]["tools"]] == ["suggest_steps"]


def test_turn_reports_its_phases_before_anything_shows():
    engine = ChatEngine(FakeProvider([wider_reply()]))
    events = run(engine, Chat(), "make it wider", session())
    started, reading, thinking = events[:3]
    assert isinstance(started, TurnStarted)
    assert (reading, thinking) == (TurnStatus(started.turn_id, "reading"),
                                   TurnStatus(started.turn_id, "thinking"))
    # The steps phase comes after the prose and before the steps themselves.
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("TurnStatus", 3) > kinds.index("TurnText")
    assert events[kinds.index("TurnStatus", 3)].phase == "steps"
    assert kinds.index("TurnStatus", 3) < kinds.index("TurnSteps")


def test_steps_are_checked_and_stored():
    engine = ChatEngine(FakeProvider([wider_reply()]))
    chat = Chat()
    events = run(engine, chat, "make this wider", session())
    steps = next(e for e in events if isinstance(e, TurnSteps))
    assert steps.warnings == []
    assert [s.track for s in steps.steps] == ["Warm Pad", "Warm Pad"]
    assert [s.track_index for s in steps.steps] == [2, 2]
    answer = chat.turns[-1]
    assert answer.listen_for and answer.caution and len(answer.steps) == 2
    assert set(answer.snapshots) == {"2"}


def test_context_block_has_summary_and_detail():
    provider = FakeProvider([wider_reply()])
    live = session()
    run(ChatEngine(provider), Chat(context=ContextSet(pinned=[0])), "make this wider", live)
    call = provider.calls[0]
    assert "Chorus-Ensemble" in call["system"] and "suggest_steps" in call["system"]
    msg = call["messages"][-1]["content"]
    assert msg.startswith("<context>") and msg.rstrip().endswith('(Knowledge-map terms that match: "wide".)')
    assert '"Sub Bass" (midi): Operator, Saturator' in msg
    assert "selected in Live, full detail below" in msg and "pinned, full detail below" in msg
    assert "Full detail (selected in Live):" in msg and "Stereo Width: 100 %" in msg
    assert "Full detail (pinned):" in msg and 'Track: "Lead Vox"' in msg
    assert "Saturator\n" not in msg.split("Full detail")[1]  # the bass has no full detail


def test_steps_on_a_summary_only_track_are_checked():
    steps = copy.deepcopy(WIDER_PAD["steps"])
    steps[0].update(track="Sub Bass", device="Saturator", parameter="Drive")
    steps[1].update(track="Sub Bass", device="Reverb")  # not on the bass
    provider = FakeProvider([Reply.with_steps("Push the bass saturator.", steps, "Bypass it.")])
    events = run(ChatEngine(provider), Chat(), "warmer bass", session())
    warnings = next(e for e in events if isinstance(e, TurnSteps)).warnings
    assert warnings == ["Step 2: says Reverb is on the track, but it isn't."]


def test_summary_only_track_gets_its_index():
    steps = copy.deepcopy(WIDER_PAD["steps"])[:1]
    steps[0].update(track="sub bass", device="Saturator", parameter="Drive")
    provider = FakeProvider([Reply.with_steps("Push it.", steps, "Bypass it.")])
    events = run(ChatEngine(provider), Chat(), "warmer bass", session())
    step = next(e for e in events if isinstance(e, TurnSteps)).steps[0]
    assert (step.track, step.track_index) == ("Sub Bass", 1)


def test_ambiguous_or_missing_track_leaves_index_empty():
    pad, twin = load("synth_pad"), load("synth_pad")
    twin.track.index = 5
    live = StaticLive([pad, twin, load("lead_vocal")], selected=None)
    steps = copy.deepcopy(WIDER_PAD["steps"])
    steps[0]["track"] = "Warm Pad"  # two tracks have this name
    provider = FakeProvider([Reply.with_steps("Widen it.", steps, "Bypass it.")])
    events = run(ChatEngine(provider), Chat(context=ContextSet(pinned=[2, 5])), "wider", live)
    got = next(e for e in events if isinstance(e, TurnSteps))
    assert [s.track_index for s in got.steps] == [None, None]
    assert "Step 1: 'Warm Pad' matches more than one track." in got.warnings
    assert "Step 2: doesn't say which track it's on." in got.warnings


def test_unknown_track_is_flagged():
    steps = copy.deepcopy(WIDER_PAD["steps"])[:1]
    steps[0]["track"] = "Strings"
    provider = FakeProvider([Reply.with_steps("Widen it.", steps, "Bypass it.")])
    events = run(ChatEngine(provider), Chat(), "wider", session())
    warnings = next(e for e in events if isinstance(e, TurnSteps)).warnings
    assert any("Strings" in w for w in warnings)


def test_refine_puts_the_diff_and_history_in_the_prompt():
    provider = FakeProvider([wider_reply(), Reply("Nice, that width is about right.")])
    engine = ChatEngine(provider)
    live = session()
    chat = Chat()
    run(engine, chat, "make this wider", live)

    pad = live.states[2].model_copy(deep=True)
    utility = next(d for d in pad.track.devices if d.name == "Utility")
    next(p for p in utility.parameters if p.name == "Stereo Width").display = "130 %"
    pad.track.devices.append(Device(name="Reverb", class_name="Reverb"))
    live.states[2] = pad
    run(engine, chat, "better?", live)

    messages = provider.calls[1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "make this wider"  # older turns go back as text only
    assert "[Steps I suggested:\n1. On Utility on \"Warm Pad\" > Stereo Width: 120-140 %" in messages[1]["content"]
    latest = messages[2]["content"]
    assert "Since your last answer, they changed:" in latest
    assert '- "Warm Pad": added Reverb' in latest
    assert '- "Warm Pad": Utility > Stereo Width: 100 % -> 130 %' in latest
    assert chat.title == "Scripted chat" and len(chat.turns) == 4


def test_error_turn():
    provider = FakeProvider([Reply("Half an", error="Claude declined this request."), Reply("Fine.")])
    engine = ChatEngine(provider)
    chat = Chat()
    events = run(engine, chat, "wider", session())
    assert isinstance(events[-2], TurnError) and isinstance(events[-1], TurnDone)
    assert chat.title is None and chat.turns[-1].error and chat.turns[-1].text == "Half an"
    assert chat.last_answer() is None
    run(engine, chat, "try again", session())
    assert chat.title == "Scripted chat"


def test_live_failures_become_notes():
    class Broken:
        def session_summary(self):
            raise OSError("no answer")

        def selected_track_index(self):
            return 3

        def track_state(self, index):
            raise OSError("timeout")

    provider = FakeProvider()
    run(ChatEngine(provider), Chat(), "wider", Broken())
    msg = provider.calls[0]["messages"][-1]["content"]
    assert "Couldn't read the session from Live (no answer)." in msg
    assert "Couldn't read track 3 from Live (timeout)." in msg


def test_dict_shaped_live_is_accepted():
    pad = load("synth_pad")

    class DictLive:
        def session_summary(self):
            return {"schema_version": 1, "live_version": "11.3", "tempo": 120,
                    "tracks": [{"index": 2, "name": "Warm Pad", "type": "midi",
                                "devices": ["Wavetable"], "volume": 0.85}]}

        def selected_track_index(self):
            return 2

        def track_state(self, index):
            return pad.model_dump()

    provider = FakeProvider()
    run(ChatEngine(provider), Chat(), "wider", DictLive())
    assert "Session: Live 11.3, 120 BPM." in provider.calls[0]["messages"][-1]["content"]


def test_chat_file_round_trip(tmp_path):
    chat = Chat()
    run(ChatEngine(FakeProvider([wider_reply()])), chat, "make this wider", session())
    path = tmp_path / "chat.json"
    chat.save(path)
    again = Chat.load(path)
    assert again == chat
    assert isinstance(again.turns[1], AssistantTurn)
    assert '"schema_version": 1' in path.read_text()
    assert again.summary().id == chat.id


def test_newer_chat_file_is_refused():
    with pytest.raises(ValueError, match="newer"):
        Chat.from_json('{"schema_version": 2, "id": "x"}')


def test_diff_tracks():
    old = load("drum_group")
    new = old.model_copy(deep=True)
    new.track.devices.reverse()
    eq = next(d for d in new.track.devices if d.name == "EQ Eight")
    next(p for p in eq.parameters if p.name == "Device On").value = 0
    new.track.mixer.volume = 0.7
    new.track.mixer.mute = True
    changes = diff_tracks(old, new)
    assert "reordered the chain: Glue Compressor, EQ Eight" in changes
    assert "turned EQ Eight off" in changes
    assert "moved the fader (0.85 -> 0.70, raw 0..1)" in changes
    assert "muted the track" in changes
    assert diff_tracks(old, old) == []


def test_diff_inside_racks():
    old = load("drum_rack")
    new = old.model_copy(deep=True)
    rack = new.track.devices[0]
    chain = rack.chains[0]
    chain.devices.append(Device(name="Saturator", parameters=[Parameter(name="Drive", display="3 dB")]))
    changes = diff_tracks(old, new)
    assert changes == [f"added {rack.name} > {chain.name} > Saturator"]


def test_title_failure_is_ignored():
    class NoTitle(FakeProvider):
        def generate_text(self, system, user):
            raise RuntimeError("down")

    chat = Chat()
    events = run(ChatEngine(NoTitle()), chat, "hi", None)
    assert chat.title is None and not any(isinstance(e, ChatTitled) for e in events)


def test_title_is_cleaned():
    engine = ChatEngine(FakeProvider(title='"Wider pad without mud."\nextra'))
    assert engine.title_for("wider", "...") == "Wider pad without mud"


def test_cli_chat(monkeypatch, capsys, tmp_path):
    import io

    import vibe2knob.cli as cli
    from vibe2knob.cli import main

    monkeypatch.setattr(cli, "get_provider", lambda name, model: FakeProvider([wider_reply()]))
    monkeypatch.setattr("sys.stdin", io.StringIO("make this wider\n"))
    out_file = tmp_path / "chat.json"
    assert main(["chat", "-s", str(FIXTURES / "synth_pad.json"), "--save", str(out_file)]) == 0
    out = capsys.readouterr().out
    assert "Chatting about: Warm Pad" in out
    assert "1. On Utility on \"Warm Pad\" > Stereo Width: 120-140 %" in out
    assert "(topic: Scripted chat)" in out
    assert len(Chat.load(out_file).turns) == 2


def test_engine_reads_the_real_fake_live():
    from live_bridge.fake import FakeLive

    live = FakeLive()  # every fixture, in file-name order
    pad = next(t["index"] for t in live.session_summary()["tracks"] if t["name"] == "Warm Pad")
    live.select(pad)
    provider = FakeProvider([wider_reply()])
    chat = Chat()
    events = run(ChatEngine(provider), chat, "make this wider", live)
    steps = next(e for e in events if isinstance(e, TurnSteps))
    assert steps.warnings == [] and {s.track_index for s in steps.steps} == {pad}
    msg = provider.calls[0]["messages"][-1]["content"]
    assert "Session: Live 11.3" in msg and "Full detail (selected in Live):" in msg

    live.set_connected(False)
    run(ChatEngine(provider), chat, "and now?", live)
    assert "Couldn't read the session from Live" in provider.calls[1]["messages"][-1]["content"]
