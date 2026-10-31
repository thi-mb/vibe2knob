import json

import pytest
from pydantic import ValidationError

from vibe2knob import protocol
from vibe2knob.protocol import (
    ChatDelta, ChatSend, ChatSteps, Settings, parse_client, parse_hub, schema,
)

from .conftest import ROOT


def test_client_message_round_trip():
    msg = parse_client('{"v": 1, "type": "chat.send", "chat_id": "c1", "text": "wider"}')
    assert isinstance(msg, ChatSend)
    assert msg.text == "wider"


def test_version_defaults_and_is_enforced():
    assert parse_client('{"type": "tracks.list"}').v == 1
    with pytest.raises(ValidationError):
        parse_client('{"v": 2, "type": "tracks.list"}')


def test_unknown_type_and_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        parse_client('{"type": "live.delete_everything"}')
    with pytest.raises(ValidationError):
        parse_client('{"type": "chat.send", "chat_id": "c1", "text": "x", "surprise": 1}')


def test_hub_messages_serialize_with_version_and_type():
    data = json.loads(ChatDelta(chat_id="c1", turn_id="t1", text="Spread").model_dump_json())
    assert data == {"v": 1, "type": "chat.delta", "chat_id": "c1", "turn_id": "t1", "text": "Spread"}
    assert isinstance(parse_hub(json.dumps(data)), ChatDelta)


def test_steps_message_carries_the_advice_step_shape():
    step = {
        "action": "adjust", "device": "Utility", "chain": None, "on_track": True,
        "parameter": "Width", "value_range": "120-140 %", "explanation": "Wider sides.",
    }
    msg = parse_hub(json.dumps({"type": "chat.steps", "chat_id": "c", "turn_id": "t", "steps": [step]}))
    assert isinstance(msg, ChatSteps)
    assert msg.steps[0].parameter == "Width"


def test_settings_never_carry_the_key():
    assert "api_key" not in Settings.model_fields


def test_ui_schema_is_up_to_date():
    """ui/src/protocol/schema.json is generated; run `npm run gen:types` in ui/ after a change."""
    committed = json.loads((ROOT / "ui" / "src" / "protocol" / "schema.json").read_text())
    assert committed == schema(), "Protocol changed: run `npm run gen:types` in ui/"


def test_schema_marks_type_required_for_narrowing():
    defs = schema()["$defs"]
    for name in ("ChatSend", "Hello", "ChatDelta", "ErrorMessage"):
        assert "type" in defs[name]["required"], name
    assert "v" in defs["ChatDelta"]["required"]
    assert protocol.PROTOCOL_VERSION == 1


def test_chat_history_replays_turns_with_their_turn_ids():
    msg = parse_hub(json.dumps({
        "type": "chat.history", "chat_id": "c", "turns": [
            {"id": "u1", "role": "user", "text": "wider", "created_at": "2026-10-21T10:00:00Z"},
            {"id": "t1", "role": "assistant", "text": "Spread it.",
             "created_at": "2026-10-21T10:00:01Z", "steps": None},
        ],
    }))
    assert [t.id for t in msg.turns] == ["u1", "t1"]
    assert msg.turns[0].steps is None and msg.turns[0].warnings == []
    assert msg.turns[1].error is None
    assert isinstance(parse_client('{"type": "chat.open", "chat_id": "c"}'), protocol.ChatOpen)


def test_example_tracks_are_additive():
    use = parse_client('{"type": "live.use_example", "name": null}')
    assert isinstance(use, protocol.LiveUseExample) and use.name is None
    ref = protocol.TrackRef(index=0, name="Warm Pad", type="midi")
    assert ref.devices == [] and protocol.LiveStatus(connected=False).example is None


def test_v02_reads_and_budget_are_additive():
    status = parse_hub('{"type": "chat.status", "chat_id": "c", "turn_id": "t", "phase": "reading"}')
    assert status.detail is None
    status = parse_hub(json.dumps({"type": "chat.status", "chat_id": "c", "turn_id": "t",
                                   "phase": "reading", "detail": "Reading Kick, Sub Bass"}))
    assert status.detail == "Reading Kick, Sub Bass"

    looked = parse_hub(json.dumps({"type": "chat.looked", "chat_id": "c", "turn_id": "t",
                                   "tracks": [{"index": 0, "name": "Kick", "type": "audio"}]}))
    assert isinstance(looked, protocol.ChatLooked) and looked.tracks[0].name == "Kick"

    turn = protocol.ChatTurn(id="t", role="assistant", text="x", created_at="t")
    assert turn.looked_at == []
    assert protocol.Settings().monthly_budget_usd is None
    update = parse_client('{"type": "settings.update", "monthly_budget_usd": 10}')
    assert update.monthly_budget_usd == 10.0

    budget = parse_hub('{"type": "usage.budget", "month_cost_usd": 12.5, "limit_usd": 10, "over": true}')
    assert isinstance(budget, protocol.UsageBudget) and budget.over
    assert "ChatLooked" in schema()["$defs"] and "UsageBudget" in schema()["$defs"]
    assert protocol.PROTOCOL_VERSION == 1


# Do mode and voice


def test_do_mode_messages_parse():
    m = parse_client('{"type": "chat.set_mode", "chat_id": "c", "mode": "do"}')
    assert m.mode == "do"
    with pytest.raises(ValidationError):
        parse_client('{"type": "chat.set_mode", "chat_id": "c", "mode": "yolo"}')
    for kind in ("action.confirm", "action.skip", "action.undo"):
        m = parse_client(json.dumps({"type": kind, "chat_id": "c", "action_ids": ["a1", "a2"]}))
        assert m.action_ids == ["a1", "a2"]
        with pytest.raises(ValidationError):
            parse_client(json.dumps({"type": kind, "chat_id": "c", "action_ids": []}))
    assert parse_client('{"type": "step.apply", "chat_id": "c", "turn_id": "t", "step_index": 1}'
                        ).step_index == 1
    with pytest.raises(ValidationError):
        parse_client('{"type": "step.apply", "chat_id": "c", "turn_id": "t", "step_index": -1}')
    m = parse_client('{"type": "settings.update", "default_mode": "do", "auto_actions": ["mute"]}')
    assert m.auto_actions == ["mute"]
    with pytest.raises(ValidationError):
        parse_client('{"type": "settings.update", "auto_actions": ["delete_track"]}')


def test_chat_actions_round_trip():
    track = {"index": 2, "name": "Pad", "type": "midi"}
    action = {"id": "a1", "kind": "volume", "track": track, "before": "-6.0 dB", "after": "-3.0 dB"}
    m = parse_hub(json.dumps({"type": "chat.actions", "chat_id": "c", "turn_id": "t",
                              "actions": [action]}))
    assert isinstance(m, protocol.ChatActions)
    a = m.actions[0]
    assert a.status == "proposed" and a.device is None and a.note is None
    data = json.loads(m.model_dump_json())
    assert data["actions"][0]["status"] == "proposed"
    assert data["actions"][0]["track"]["devices"] == []


def test_voice_messages():
    with pytest.raises(ValidationError):  # the transcript is matched by request_id
        parse_client('{"type": "voice.transcribe", "audio": "AAAA", "mime": "audio/webm"}')
    m = parse_client('{"type": "voice.transcribe", "request_id": "r", "audio": "AAAA", '
                     '"mime": "audio/webm"}')
    assert m.mime == "audio/webm"
    t = parse_hub('{"type": "voice.transcript", "request_id": "r", "text": "solo the bass"}')
    assert t.text == "solo the bass" and t.error is None


def test_v1_hub_messages_without_the_new_fields_still_parse():
    """Everything added for Do mode and voice has a default, so v1 stays v1."""
    assert protocol.PROTOCOL_VERSION == 1
    welcome = {"type": "welcome", "hub_version": "0.2.0", "chats": [{
        "id": "c", "context": {}, "updated_at": "2026-10-24T10:00:00Z"}],
        "live": {"connected": True}, "settings": {}}
    m = parse_hub(json.dumps(welcome))
    assert m.voice.available is False
    assert m.chats[0].mode == "teach" and m.live.can_write is False
    assert m.settings.default_mode == "teach" and m.settings.auto_actions == []
    turn = protocol.ChatTurn(id="t", role="assistant", text="x", created_at="t")
    assert turn.actions == []
