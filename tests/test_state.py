import pytest

from vibe2knob.state import TrackState

from .conftest import FIXTURES


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.name)
def test_fixtures_load_and_describe(path):
    state = TrackState.load(path)
    text = state.describe()
    assert state.track.name in text
    assert "Device chain" in text


def test_real_bridge_dump_with_plugins():
    state = TrackState.load(FIXTURES / "guitar_plugins.json")
    plugin, tuner = state.track.devices
    assert plugin.is_plugin and not tuner.is_plugin
    assert "parameters not visible" in state.describe()


def test_enabled_comes_from_device_on():
    state = TrackState.model_validate(
        {
            "track": {
                "name": "x",
                "type": "audio",
                "devices": [
                    {"name": "Reverb", "parameters": [{"name": "Device On", "value": 0.0}]}
                ],
            }
        }
    )
    assert state.track.devices[0].enabled is False
    assert "(OFF)" in state.describe()


def test_rack_chains_are_walked():
    state = TrackState.load(FIXTURES / "drum_rack.json")
    names = [d.name for d in state.all_devices()]
    assert "Kick 909" in names and "Reverb" in names
    assert 'chain "Snare"' in state.describe()


def test_unknown_fields_are_kept():
    state = TrackState.model_validate(
        {"schema_version": 1, "track": {"name": "x", "type": "midi", "future": 1}, "extra": True}
    )
    assert state.model_extra == {"extra": True}
