"""The agent loop: read tools, the read budget, and what a read turn leaves behind."""

import copy

from vibe2knob.chat import (
    MAX_READ_ROUNDS,
    MAX_READS,
    Chat,
    ChatEngine,
    TurnDone,
    TurnLooked,
    TurnStatus,
    TurnSteps,
    TurnText,
    render_answer,
)
from vibe2knob.providers.claude import _messages
from vibe2knob.providers.fake import FakeProvider, Reply
from vibe2knob.providers.ollama import _messages as ollama_messages, _plain
from vibe2knob.session import StaticLive, summarize
from vibe2knob.tools import BUDGET_USED, resolve_track
from vibe2knob.usage import total

from .conftest import WIDER_PAD
from .test_chat import load, run, session


def bass_steps():
    steps = copy.deepcopy(WIDER_PAD["steps"])
    for s in steps:
        s["track"] = "Sub Bass"
    return Reply.with_steps("The bass and the vocal both sit around 200 Hz.", steps, "Mute the bass.")


def test_a_read_round_then_an_answer():
    provider = FakeProvider([Reply.reads("Sub Bass", text="Let me check the bass.",
                                         reason="the low end"), bass_steps()])
    chat = Chat()
    events = run(ChatEngine(provider), chat, "the low end is muddy", session(selected=0))

    statuses = [(e.phase, e.detail) for e in events if isinstance(e, TurnStatus)]
    assert ("reading", "Reading Sub Bass") in statuses
    assert statuses[-1] == ("steps", None)
    looked = [e for e in events if isinstance(e, TurnLooked)]
    assert [[t.name for t in e.tracks] for e in looked] == [["Sub Bass"]]
    assert looked[0].tracks[0].type == "midi" and looked[0].tracks[0].devices

    # Prose of both rounds is one answer, with a break between the rounds.
    text = "".join(e.text for e in events if isinstance(e, TurnText))
    assert text == "Let me check the bass.\n\nThe bass and the vocal both sit around 200 Hz."
    turn = chat.turns[-1]
    assert turn.text == text
    assert [(t.index, t.name, t.why) for t in turn.looked_at] == [(1, "Sub Bass", "the low end")]
    assert set(turn.snapshots) == {"0", "1"}  # the selected vocal, plus the read bass

    # Round 2 resends round 1 and the read result after the user's message.
    assert len(provider.calls) == 2
    second = provider.calls[1]["messages"]
    assert second[:-2] == provider.calls[0]["messages"]
    call, result = second[-2:]
    assert call["role"] == "assistant" and call["tool_calls"][0]["name"] == "read_track"
    assert result["tool_results"][0]["id"] == call["tool_calls"][0]["id"]
    assert "Full detail (read):" in result["tool_results"][0]["content"]
    assert '"Sub Bass"' in result["tool_results"][0]["content"]
    # The tool list never changes within a turn.
    assert provider.calls[0]["tools"] == provider.calls[1]["tools"]

    # Steps on the read track are checked against its full chain.
    steps = next(e for e in events if isinstance(e, TurnSteps))
    assert not any("can't see" in w for w in steps.warnings)


def test_usage_is_one_record_summed_over_rounds():
    provider = FakeProvider([Reply.reads("Sub Bass"), Reply("Cut the bass at 250 Hz.")])
    events = run(ChatEngine(provider), Chat(), "muddy", session(selected=0))
    done = next(e for e in events if isinstance(e, TurnDone))
    assert done.usage["rounds"] == 2 and done.record.rounds == 2
    assert done.record.input_tokens == sum(
        (len(c["system"]) + sum(len(m["content"]) + sum(len(r["content"]) for r in m.get("tool_results", ()))
                                for m in c["messages"])) // 4
        for c in provider.calls
    )
    assert total([done.record]).calls == 2
    assert sum(done.record.parts.values()) == done.record.prompt_tokens + done.record.output_tokens


def test_no_read_is_one_round():
    provider = FakeProvider([Reply("Open up the Utility.")])
    events = run(ChatEngine(provider), Chat(), "wider", session())
    assert len(provider.calls) == 1
    assert not any(isinstance(e, TurnLooked) for e in events)
    assert next(e for e in events if isinstance(e, TurnDone)).usage["rounds"] == 1


def test_read_budget_is_told_to_the_model():
    names = ["Lead Vox", "Sub Bass", "Drums"]
    provider = FakeProvider([Reply.reads(n) for n in names] + [Reply.reads("Warm Pad")]
                            + [Reply("Here's what I'd do.")])
    chat = Chat()
    run(ChatEngine(provider), chat, "how does the mix sit?", session(selected=None))
    # Three read rounds, a fourth whose reads are refused, then the answer.
    assert len(provider.calls) == MAX_READ_ROUNDS + 2
    refused = provider.calls[-1]["messages"][-1]["tool_results"][0]["content"]
    assert refused == BUDGET_USED
    assert [t.name for t in chat.turns[-1].looked_at] == names
    assert chat.turns[-1].text == "Here's what I'd do."


def test_the_turn_ends_if_the_model_keeps_reading():
    provider = FakeProvider([Reply.reads("Sub Bass")])  # repeats forever
    run(ChatEngine(provider), Chat(), "?", session(selected=None))
    assert len(provider.calls) == MAX_READ_ROUNDS + 2


def test_reads_per_turn_are_capped():
    states = [load(n) for n in ("lead_vocal", "sub_bass", "synth_pad", "drum_group", "drum_rack")]
    live = StaticLive(states, selected=None)
    provider = FakeProvider([
        Reply.reads("Lead Vox", "Sub Bass", "Warm Pad"),
        Reply.reads("Drums", "Beat", "Lead Vox"),
        Reply("Done."),
    ])
    chat = Chat()
    run(ChatEngine(provider), chat, "everything", live)
    assert len(chat.turns[-1].looked_at) == 5  # Lead Vox only once
    second = provider.calls[2]["messages"][-1]["tool_results"][0]["content"]
    assert '"Lead Vox" already has full detail' in second
    assert MAX_READS >= 5


def test_reading_a_track_with_full_detail_is_a_no_op():
    provider = FakeProvider([Reply.reads("Warm Pad"), Reply("ok")])
    chat = Chat()
    events = run(ChatEngine(provider), chat, "wider", session(selected=2))
    assert not any(isinstance(e, TurnLooked) for e in events)
    assert "already has full detail" in provider.calls[1]["messages"][-1]["tool_results"][0]["content"]


def test_unknown_and_ambiguous_names_give_candidates():
    summary = summarize([load("lead_vocal"), load("sub_bass"), load("synth_pad"), load("drum_group")])
    assert resolve_track("Sub Bass", summary) == (1, [])
    assert resolve_track("sub bass", summary) == (1, [])
    assert resolve_track("warm", summary) == (2, [])
    assert resolve_track("Lead Vocal", summary)[0] is None
    assert "Lead Vox" in resolve_track("Lead Vocal", summary)[1]
    assert resolve_track("Theremin", summary) == (None, [])

    provider = FakeProvider([Reply.reads("Theremin"), Reply("ok")])
    run(ChatEngine(provider), Chat(), "?", session())
    assert 'No track is called "Theremin"' in provider.calls[1]["messages"][-1]["tool_results"][0]["content"]


def test_ambiguous_prefix_lists_both():
    states = [load("lead_vocal"), load("sub_bass")]
    states[0].track.name = "Bass Synth"
    summary = summarize(states)
    assert resolve_track("bass", summary) == (0, [])  # "Sub Bass" doesn't start with it
    states[1].track.name = "Bass Sub"
    summary = summarize(states)
    assert resolve_track("bass", summary) == (None, ["Bass Synth", "Bass Sub"])


def test_without_live_a_read_says_so():
    provider = FakeProvider([Reply.reads("Kick"), Reply("ok")])
    chat = Chat()
    run(ChatEngine(provider), chat, "?", None)
    assert "isn't connected" in provider.calls[1]["messages"][-1]["tool_results"][0]["content"]


def test_history_notes_what_was_read():
    provider = FakeProvider([Reply.reads("Sub Bass"), Reply("Cut it."), Reply("Sure.")])
    engine = ChatEngine(provider)
    chat = Chat()
    run(engine, chat, "muddy", session(selected=0))
    assert render_answer(chat.turns[-1]).endswith("(looked at Sub Bass)")
    run(engine, chat, "and now?", session(selected=0))
    history = provider.calls[2]["messages"]
    assert history[1] == {"role": "assistant", "content": "Cut it.\n\n(looked at Sub Bass)"}
    assert all("tool_calls" not in m for m in history)


def test_saved_chat_keeps_looked_at():
    provider = FakeProvider([Reply.reads("Sub Bass"), Reply("Cut it.")])
    chat = Chat()
    run(ChatEngine(provider), chat, "muddy", session(selected=0))
    again = Chat.from_json(chat.to_json())
    assert again.turns[-1].looked_at[0].name == "Sub Bass"
    # Chats saved before reads load with an empty list.
    data = chat.model_dump()
    del data["turns"][-1]["looked_at"]
    assert Chat.model_validate(data).turns[-1].looked_at == []


def test_claude_messages_carry_tool_blocks_and_the_cache_breakpoint():
    raw = [{"type": "thinking", "thinking": "", "signature": "s"},
           {"type": "tool_use", "id": "t1", "name": "read_track", "input": {"tracks": ["Kick"]}}]
    out = _messages([
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "<context>...</context> muddy"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "read_track",
                                                              "input": {"tracks": ["Kick"]}}],
         "raw": raw},
        {"role": "user", "content": "", "tool_results": [{"id": "t1", "name": "read_track",
                                                           "content": "Full detail"}]},
    ])
    assert out[0] == {"role": "user", "content": "earlier"}
    assert out[3]["content"] is raw  # thinking blocks go back unchanged
    assert out[4]["content"] == [{"type": "tool_result", "tool_use_id": "t1", "content": "Full detail",
                                  "cache_control": {"type": "ephemeral"}}]
    single = _messages([{"role": "user", "content": "hi"}])
    assert single == [{"role": "user", "content": [
        {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}]
    no_raw = _messages([{"role": "assistant", "content": "Let me look.", "tool_calls": [
        {"id": "t1", "name": "read_track", "input": {}}]}])
    assert [b["type"] for b in no_raw[0]["content"]] == ["text", "tool_use"]


def test_ollama_messages_use_tool_role():
    msgs = [
        {"role": "user", "content": "muddy"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c", "name": "read_track",
                                                              "input": {"tracks": ["Kick"]}}]},
        {"role": "user", "content": "", "tool_results": [{"id": "c", "name": "read_track",
                                                           "content": "Full detail"}]},
    ]
    out = ollama_messages(msgs)
    assert out[1]["tool_calls"] == [{"function": {"name": "read_track", "arguments": {"tracks": ["Kick"]}}}]
    assert out[2] == {"role": "tool", "content": "Full detail", "tool_name": "read_track"}
    plain = _plain(msgs)
    assert [m["role"] for m in plain] == ["user", "assistant", "user"]
    assert "Full detail" in plain[2]["content"] and "read_track" in plain[1]["content"]
