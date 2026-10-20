from vibe2knob.knowledge import load_terms, match_terms, render_map
from vibe2knob.stock_devices import canonical, is_stock


def test_every_technique_uses_stock_devices():
    for term in load_terms():
        assert term.techniques, term.id
        for t in term.techniques:
            for d in t.devices:
                assert is_stock(d), f"{term.id}/{t.name}: {d}"
            for p in t.params:
                assert p.device in t.devices, f"{term.id}/{t.name}: {p.device}"


def test_term_ids_unique():
    ids = [t.id for t in load_terms()]
    assert len(ids) == len(set(ids))


def test_match_terms_in_order():
    terms = load_terms()
    ids = [t.id for t in match_terms("Make this trippier but also WIDER, it's muddy", terms)]
    assert ids == ["trippy", "wide", "clean"]


def test_match_terms_whole_words():
    terms = load_terms()
    # "airport" should not trigger "air", "punchline" should not trigger "punch"
    assert match_terms("airport punchline", terms) == []


def test_render_map_mentions_devices():
    text = render_map(load_terms())
    assert "Utility" in text and "Phaser-Flanger" in text


def test_canonical_names():
    assert canonical("StereoGain") == "Utility"
    assert canonical("chorus") == "Chorus-Ensemble"
    assert canonical("eq eight") == "EQ Eight"
    assert canonical("Serum") is None
