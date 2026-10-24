import json
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
        for f in [c.fixture, c.fixture_after, *c.session, *c.pinned]:
            if f:
                assert (CASES.parent / f).exists(), c.id
        assert c.fixture or c.chat_only, c.id


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


def test_chat_path_runs_every_case(tmp_path):
    from vibe2knob.providers.fake import FakeProvider, Reply

    cases = load_cases(CASES)
    reply = Reply.with_steps(
        "Widen the pad but keep the low end mono.",
        copy.deepcopy(WIDER_PAD["steps"]), WIDER_PAD["listen_for"], WIDER_PAD["caution"],
    )
    provider = FakeProvider([reply])
    results = run_cases(cases, provider, CASES.parent, report_dir=tmp_path, echo=lambda _: None, chat=True)
    assert len(results) == len(cases)
    by_id = {r.case.id: r for r in results}
    assert by_id["pad-wider"].passed
    assert not by_id["chat-general-question"].passed  # steps where none belong
    assert (tmp_path / "fake-scripted-chat.json").exists()
    refine = next(c for c in provider.calls if "ok it's wider now" in c["messages"][-1]["content"])
    assert [m["role"] for m in refine["messages"]] == ["user", "assistant", "user"]
    assert "Utility > Stereo Width: 100 % -> 140 %" in refine["messages"][-1]["content"]
    two = next(c for c in provider.calls if "fighting" in c["messages"][-1]["content"])
    assert '"Warm Pad" (midi): Wavetable, Chorus-Ensemble, Utility' in two["messages"][-1]["content"]


def test_ask_path_skips_chat_only_cases(fake):
    cases = load_cases(CASES)
    results = run_cases(cases, fake(WIDER_PAD), CASES.parent, echo=lambda _: None)
    assert all(not r.case.chat_only for r in results)
    assert len(results) == 15


def test_read_checks():
    from vibe2knob.evals import Case, grade_reads

    case = Case(id="x", fixture="f", request="r", expect_reads_any=["Sub Bass", "Beat"],
                expect_reads_all=["Lead Vox"], forbid_reads=["Warm Pad"])
    assert all(c.passed for c in grade_reads(case, ["Beat", "Lead Vox"]))
    failed = {c.name for c in grade_reads(case, ["Warm Pad"]) if not c.passed}
    assert failed == {"reads a track the ask depends on", "reads every track the ask names",
                      "doesn't read for its own sake"}
    assert grade_reads(Case(id="y", fixture="f", request="r"), ["Beat"]) == []


def test_read_cases_run_on_the_chat_path(tmp_path):
    from vibe2knob.evals import load_cases, run_cases
    from vibe2knob.providers.fake import FakeProvider, Reply

    cases = [c for c in load_cases(CASES) if c.id == "chat-reads-both-clash"]
    provider = FakeProvider([
        Reply.reads("guitar", "Lead Vox"),
        Reply.reads("acoustic-pick"),
        Reply("They meet around 2 to 4 kHz, where the guitar's pick and the voice's consonants sit, "
              "so carve a little space in the guitar there and listen in context."),
    ])
    [result] = run_cases(cases, provider, CASES.parent, report_dir=tmp_path, echo=lambda *_: None, chat=True)
    assert result.reads == ["Lead Vox", "acoustic-pick"]
    assert result.passed, [c for c in result.checks if not c.passed]
    report = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert report[0]["reads"] == ["Lead Vox", "acoustic-pick"]
    # "guitar" isn't a track name, so the model was given the closest names to choose from.
    first = provider.calls[1]["messages"][-1]["tool_results"][0]["content"]
    assert 'No track is called "guitar"' in first or '"guitar" could be' in first
