import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


class FakeProvider:
    """Returns a canned answer and records what it was asked."""

    name = "fake"
    model = "canned"

    def __init__(self, answer: dict):
        self.answer = answer
        self.calls = []

    def generate(self, system, user, schema):
        self.calls.append((system, user, schema))
        return json.dumps(self.answer)


WIDER_PAD = {
    "interpretation": "Spread the pad further across the stereo field.",
    "steps": [
        {
            "action": "adjust",
            "device": "Utility",
            "on_track": True,
            "parameter": "Stereo Width",
            "value_range": "120-140 %",
            "explanation": "Raises the side signal so the pad spreads wider around the centre.",
        },
        {
            "action": "adjust",
            "device": "Chorus-Ensemble",
            "on_track": True,
            "parameter": "Mode",
            "value_range": "Ensemble",
            "explanation": "Ensemble uses three modulated voices, which gives a lusher, wider spread.",
        },
    ],
    "listen_for": "Bypass Utility and listen to how far the pad reaches toward the speakers.",
    "caution": "Turn on Bass Mono so the low end of the pad stays mono.",
}


@pytest.fixture
def fake():
    return FakeProvider
