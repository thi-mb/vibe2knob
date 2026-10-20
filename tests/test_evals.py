import copy
from pathlib import Path

from vibe2knob.advice import Advice
from vibe2knob.evals import grade, load_cases, run_cases

from .conftest import ROOT, WIDER_PAD

CASES = ROOT / "evals" / "cases.toml"


def test_cases_point_at_real_fixtures():
    cases = load_cases(CASES)
    assert len(cases) >= 10
    assert len({c.id for c in cases}) == len(cases)
    for c in cases:
        assert (CASES.parent / c.fixture).exists(), c.id


def test_good_answer_passes():
    case = next(c for c in load_cases(CASES) if c.id == "pad-wider")
    checks = grade(case, Advice.model_validate(WIDER_PAD))
    assert all(c.passed for c in checks), [c for c in checks if not c.passed]


def test_bad_answer_fails():
    case = next(c for c in load_cases(CASES) if c.id == "pad-wider")
    bad = copy.deepcopy(WIDER_PAD)
    bad["steps"] = bad["steps"][:1]
    bad["steps"][0]["on_track"] = False
    bad["steps"][0]["device"] = "Reverb"
    bad["caution"] = None
    bad["listen_for"] = "Listen."
    failed = {c.name for c in grade(case, Advice.model_validate(bad)) if not c.passed}
    assert {"step count", "works with what's on the track", "mentions the key point", "A/B listening tip"} <= failed


def test_run_cases_writes_report(tmp_path, fake):
    cases = [c for c in load_cases(CASES) if c.id == "pad-wider"]
    lines = []
    results = run_cases(cases, fake(WIDER_PAD), CASES.parent, report_dir=tmp_path, echo=lines.append)
    assert results[0].passed
    assert (tmp_path / "fake-canned.json").exists()
    assert lines[0].startswith("PASS")
