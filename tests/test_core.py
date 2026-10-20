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
    answer["steps"][1]["device"] = "chorus"
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
