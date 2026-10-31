import copy
import json

import pytest

from vibe2knob.advice import ADVICE_SCHEMA, Advice, format_advice
from vibe2knob.cli import main
from vibe2knob.core import ask
from vibe2knob.providers import ProviderError
from vibe2knob.state import TrackState

from .conftest import FIXTURES, WIDER_PAD


def pad():
    return TrackState.load(FIXTURES / "synth_pad.json")


def test_ask_returns_structured_advice(fake):
    provider = fake(WIDER_PAD)
    advice = ask(pad(), "make this wider", provider)
    assert isinstance(advice, Advice)
    assert advice.warnings == []
    system, user, schema = provider.calls[0]
    assert "Warm Pad" in user and "make this wider" in user
    assert '"wide"' in user
    assert "Chorus-Ensemble" in system
    assert schema is ADVICE_SCHEMA


def test_non_stock_and_missing_devices_are_flagged(fake):
    answer = copy.deepcopy(WIDER_PAD)
    answer["steps"][0]["device"] = "Serum"
    answer["steps"][1]["device"] = "Reverb"  # claims to be on the track, but isn't
    advice = ask(pad(), "wider", fake(answer))
    assert any("not a stock" in w for w in advice.warnings)
    assert any("isn't" in w for w in advice.warnings)


def test_device_names_are_normalised(fake):
    answer = copy.deepcopy(WIDER_PAD)
    answer["steps"][1]["device"] = "chorus ensemble"
    advice = ask(pad(), "wider", fake(answer))
    assert advice.steps[1].device == "Chorus-Ensemble"
    assert advice.warnings == []


def test_bad_model_output_raises(fake):
    with pytest.raises(ProviderError):
        ask(pad(), "wider", fake({"nope": 1}))


def test_schema_is_strict():
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])

    walk(ADVICE_SCHEMA)


def test_format_advice():
    text = format_advice(Advice.model_validate(WIDER_PAD))
    assert "1. On Utility > Stereo Width: 120-140 %" in text
    assert "Listen:" in text


def test_cli_dry_run(capsys):
    assert main(["ask", "make it trippier", "--state", str(FIXTURES / "synth_pad.json"), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "=== system ===" in out and '"trippy"' in out


def test_cli_json_with_provider(monkeypatch, capsys, fake):
    import vibe2knob.cli as cli

    monkeypatch.setattr(cli, "get_provider", lambda name, model: fake(WIDER_PAD))
    assert main(["ask", "wider", "-s", str(FIXTURES / "synth_pad.json"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["steps"][0]["device"] == "Utility"


def test_cli_reports_errors(capsys):
    assert main(["ask", "wider", "-s", "does-not-exist.json"]) == 2
    assert "error:" in capsys.readouterr().err


def test_adjusting_a_plugin_on_the_track_is_allowed(fake):
    state = TrackState.load(FIXTURES / "acoustic_native_fx.json")
    answer = copy.deepcopy(WIDER_PAD)
    answer["steps"][0].update(device="Ozone Imager 2", parameter="Width", on_track=True)
    answer["steps"][1].update(device="Chorus", parameter="Delay 2 Time", on_track=True)
    advice = ask(state, "wider", fake(answer))
    assert advice.warnings == []
    assert advice.steps[1].device == "Chorus"  # legacy device, not Chorus-Ensemble

    answer["steps"][0]["action"] = "add_device"
    advice = ask(state, "wider", fake(answer))
    assert any("not a stock" in w for w in advice.warnings)


def test_prompt_flags_live_12_only_devices(fake):
    provider = fake(WIDER_PAD)
    ask(pad(), "wider", provider)
    system = provider.calls[0][0]
    assert "only in Live 12" in system and "Roar" in system


def test_device_added_by_an_earlier_step_can_be_adjusted(fake):
    answer = copy.deepcopy(WIDER_PAD)
    added = {**answer["steps"][0], "action": "add_device", "device": "Hybrid Reverb", "on_track": False}
    tweak = {**added, "action": "adjust", "parameter": "Decay"}
    answer["steps"] = [added, tweak]
    advice = ask(pad(), "dreamy", fake(answer))
    assert advice.warnings == []


def test_decorated_names_resolve_to_chain_devices(fake):
    # Seen from a real model: notes appended to the device name.
    state = TrackState.load(FIXTURES / "drum_rack.json")
    answer = copy.deepcopy(WIDER_PAD)
    answer["steps"][0].update(device="Kick 909 (OriginalSimpler, Kick chain)", parameter="Ve Decay")
    answer["steps"][1].update(device="Reverb (in Snare chain)", parameter="Decay Time")
    advice = ask(state, "tighter", fake(answer))
    assert advice.warnings == []
    assert [s.device for s in advice.steps] == ["Kick 909", "Reverb"]
    assert [s.stock_device for s in advice.steps] == ["Simpler", "Reverb"]


def test_new_device_marked_on_track_is_normalised(fake):
    # Seen from a real model: add_device steps with on_track true.
    answer = copy.deepcopy(WIDER_PAD)
    answer["steps"][0].update(action="add_device", device="Auto Filter", on_track=True)
    advice = ask(pad(), "dreamy", fake(answer))
    assert advice.warnings == []
    assert advice.steps[0].on_track is False


def _step(**fields):
    from vibe2knob.advice import Step

    base = {"action": "adjust", "on_track": True, "value_range": "0.2-0.4 s",
            "explanation": "Shorter tails keep each hit apart."}
    return Step.model_validate({**base, **fields})


def test_rack_chain_named_as_track_resolves_to_its_track():
    # Seen from a real model in a chat: the drum rack's "Kick" chain given as the track.
    from vibe2knob.core import check_steps

    beat = TrackState.load(FIXTURES / "drum_rack.json")
    vocal = TrackState.load(FIXTURES / "lead_vocal.json")
    steps = [
        _step(track="Kick", device="Kick 909", chain=None, parameter="Ve Decay"),
        _step(track="snare", device="Reverb", chain="Snare", parameter="Decay Time"),
    ]
    assert check_steps(steps, [vocal, beat]) == []
    assert [(s.track, s.track_index, s.chain) for s in steps] == [
        ("Beat", beat.track.index, "Kick"), ("Beat", beat.track.index, "Snare")]


def test_unknown_track_name_is_still_flagged():
    from vibe2knob.core import check_steps

    beat = TrackState.load(FIXTURES / "drum_rack.json")
    vocal = TrackState.load(FIXTURES / "lead_vocal.json")
    steps = [_step(track="Tom", device="Reverb", chain=None, parameter="Decay Time")]
    warnings = check_steps(steps, [vocal, beat])
    assert any("names the track 'Tom'" in w for w in warnings)
    assert steps[0].track_index is None


def test_chains_are_described_as_inside_the_rack():
    text = TrackState.load(FIXTURES / "drum_rack.json").describe()
    assert 'rack chain "Kick" (in Drum Rack):' in text
