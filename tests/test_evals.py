import copy
import json
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
    [run] = result.runs
    assert run.reads == ["Lead Vox", "acoustic-pick"]
    assert result.passed, [c for c in run.checks if not c.passed]
    assert run.rounds == 3
    report = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert report[0]["runs"][0]["reads"] == ["Lead Vox", "acoustic-pick"]
    # "guitar" isn't a track name, so the model was given the closest names to choose from.
    first = provider.calls[1]["messages"][-1]["tool_results"][0]["content"]
    assert 'No track is called "guitar"' in first or '"guitar" could be' in first


def test_repeat_reports_pass_rate_and_usage(tmp_path):
    from vibe2knob.providers.fake import FakeProvider, Reply

    cases = [c for c in load_cases(CASES) if c.id == "pad-wider"]
    good = Reply.with_steps("Widen the pad but keep the low end mono.",
                            copy.deepcopy(WIDER_PAD["steps"]), WIDER_PAD["listen_for"],
                            WIDER_PAD["caution"])
    bad = Reply("Just turn it up.")
    provider = FakeProvider([good, bad, good], title="unused")
    lines, recorded = [], []
    results = run_cases(cases, provider, CASES.parent, report_dir=tmp_path, echo=lines.append,
                        chat=True, repeat=3, record_usage=recorded.extend)
    r = results[0]
    assert (r.passes, len(r.runs), r.passed) == (2, 3, False)
    assert lines[0].startswith("FAIL 2/3")
    assert any(line.startswith("      run 2: x step count") for line in lines)
    assert not provider.title_calls  # evals skip the naming call
    assert len(recorded) == 3 and {rec.kind for rec in recorded} == {"eval"}
    row = json.loads((tmp_path / "fake-scripted-chat.json").read_text())[0]
    assert row["repeat"] == 3 and row["passes"] == 2 and row["pass_rate"] == 0.6667
    assert row["passed"] is False and row["advice"] is not None  # old fields, from run 1
    assert [run["passed"] for run in row["runs"]] == [True, False, True]
    assert row["rounds"] == 3 and row["runs"][0]["tokens"]["input"] > 0


def test_ask_path_records_usage():
    from vibe2knob.providers.fake import FakeProvider

    cases = [c for c in load_cases(CASES) if c.id == "pad-wider"]
    recorded = []
    run_cases(cases, FakeProvider(advice=WIDER_PAD), CASES.parent, echo=lambda _: None,
              record_usage=recorded.extend)
    assert [rec.kind for rec in recorded] == ["eval"]


def _row(id, passes, repeat=3):
    return {"id": id, "passes": passes, "repeat": repeat, "pass_rate": passes / repeat}


def test_compare_flags_drops():
    from vibe2knob.evals import compare

    old = [_row("a", 3), _row("b", 3), _row("c", 2)]
    lines = []
    assert compare(old, [_row("a", 2), _row("b", 3), _row("c", 3), _row("new", 0)], lines.append)
    assert "  a: 3/3 -> 2/3" in lines and "  c: 2/3 -> 3/3" in lines
    assert any("new" in line and "not compared" in line for line in lines)
    # Two thirds dropped on each of two cases: more than one case's worth.
    assert not compare(old, [_row("a", 1), _row("b", 1), _row("c", 2)], lambda _: None)
    # Exactly one case's worth is still OK.
    assert compare(old, [_row("a", 0), _row("b", 3), _row("c", 2)], lambda _: None)


def test_old_reports_load(tmp_path):
    from vibe2knob.evals import load_report

    path = tmp_path / "old.json"
    path.write_text(json.dumps([{"id": "a", "passed": True, "checks": []},
                                {"id": "b", "passed": False, "checks": []}]))
    rows = load_report(path)
    assert [(r["passes"], r["repeat"], r["pass_rate"]) for r in rows] == [(1, 1, 1.0), (0, 1, 0.0)]


def test_steps_are_capped_at_five():
    from vibe2knob.advice import ADVICE_SCHEMA, MAX_STEPS, SUGGEST_STEPS_SCHEMA
    from vibe2knob.providers.claude import _api_schema

    assert MAX_STEPS == 5
    for schema in (ADVICE_SCHEMA, SUGGEST_STEPS_SCHEMA):
        assert schema["properties"]["steps"]["maxItems"] == 5
        sent = _api_schema(schema)
        assert "maxItems" not in json.dumps(sent)
        assert sent["properties"]["steps"]["description"] == "At most 5 items."
    case = next(c for c in load_cases(CASES) if c.id == "pad-wider")
    assert (case.min_steps, case.max_steps) == (2, 5)


def test_switch_steps_need_no_value_range():
    case = next(c for c in load_cases(CASES) if c.id == "pad-wider")
    advice = Advice.model_validate(copy.deepcopy(WIDER_PAD))
    advice.steps[0].action, advice.steps[0].value_range = "enable", None
    checks = {c.name: c.passed for c in grade(case, advice)}
    assert checks["gives starting values"]
    advice.steps[0].action = "adjust"
    assert not {c.name: c.passed for c in grade(case, advice)}["gives starting values"]


def test_session_view_cases_build_the_grid():
    from vibe2knob.evals import chat_answer
    from vibe2knob.providers.fake import FakeProvider, Reply

    cases = {c.id: c for c in load_cases(CASES)}

    def context(case_id):
        provider = FakeProvider([Reply("ok")])
        chat_answer(cases[case_id], provider, CASES.parent)
        return provider.calls[-1]["messages"][-1]["content"]

    text = context("chat-scene-overlap")
    assert '"Lead Vox" and "Warm Pad" have clips together only in scene 3 "Chorus".' in text
    assert "Arrangement" not in text
    text = context("chat-playing-now")
    assert 'Playing now: all of scene 3 "Drop".' in text  # the Drums group doesn't count
    text = context("chat-arrangement-only")
    assert "never play at the same time" in text and "Session view" not in text


def test_wording_check_flags_the_summary():
    from vibe2knob.evals import grade_wording

    def answer(text):
        return Advice(interpretation=text, steps=[], listen_for="", caution=None)

    assert grade_wording(answer("Your session has a pad and a vocal."))[0].passed
    for text in ("The Summary shows a reverb on the pad.", "From the context block, the pad..."):
        check = grade_wording(answer(text))[0]
        assert not check.passed and check.name == "talks about Live, not its context"
