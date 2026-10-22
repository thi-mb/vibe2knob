"""Advice test cases: fixed tracks + requests, graded by simple, explainable checks.

The checks catch advice that is clearly wrong (non-stock devices, ignoring the
chain, missing the main pitfall). They cannot tell great advice from okay
advice, so read the saved answers in the report too.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .advice import Advice, Step
from .chat import AssistantTurn, Chat, ChatEngine, TurnError, UserTurn
from .core import ask
from .protocol import ContextSet
from .providers import Provider, ProviderError
from .arrangement import Arrangement
from .session import StaticLive
from .state import TrackState


@dataclass
class Case:
    id: str
    fixture: str
    request: str
    why: str = ""
    expect_any_device: list[str] = field(default_factory=list)
    expect_on_track_any: list[str] = field(default_factory=list)
    forbid_devices: list[str] = field(default_factory=list)
    mention_any: list[str] = field(default_factory=list)
    min_steps: int = 2
    max_steps: int = 5
    # Chat-path cases (see evals/cases.toml for how they're set up).
    chat_only: bool = False
    session: list[str] = field(default_factory=list)
    pinned: list[str] = field(default_factory=list)
    prior_request: str = ""
    prior_answer: str = ""
    prior_steps: list[dict] = field(default_factory=list)
    fixture_after: str = ""
    expect_no_steps: bool = False
    # Arrangement view, in bars from 1 (both ends included): fixture -> [[first, last], ...].
    # Unset means the Live link reports no arrangement; {} means one with no clips placed.
    plays: dict[str, list[list[int]]] | None = None
    locators: list[dict] = field(default_factory=list)  # [{name, bar}]


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Result:
    case: Case
    checks: list[Check]
    advice: Advice | None = None
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(c.passed for c in self.checks)


def load_cases(path: str | Path) -> list[Case]:
    data = tomllib.loads(Path(path).read_text())
    return [Case(**c) for c in data.get("case", [])]


def _text(advice: Advice) -> str:
    parts = [advice.interpretation, advice.listen_for, advice.caution or ""]
    for s in advice.steps:
        parts += [s.device, s.parameter or "", s.value_range or "", s.explanation]
    return " ".join(parts).lower()


def grade(case: Case, advice: Advice) -> list[Check]:
    if case.expect_no_steps:
        return [
            Check("prose only, no steps", not advice.steps, f"{len(advice.steps)} steps"),
            Check("answers in prose", len(advice.interpretation.split()) >= 15,
                  advice.interpretation[:80]),
        ] + _mention_check(case, advice)

    def names(steps):
        return [n for s in steps for n in {s.device, s.stock_device} if n]

    devices = names(advice.steps)
    checks = [
        Check(
            "step count",
            case.min_steps <= len(advice.steps) <= case.max_steps,
            f"{len(advice.steps)} steps (want {case.min_steps}-{case.max_steps})",
        ),
        Check(
            "stock devices, consistent with chain",
            not advice.warnings,
            "; ".join(advice.warnings),
        ),
        Check(
            "explains every step",
            all(len(s.explanation.split()) >= 6 for s in advice.steps),
            "some explanations are under 6 words",
        ),
        Check(
            "gives starting values",
            all(s.value_range for s in advice.steps if s.parameter),
            "a step names a parameter without a value range",
        ),
        Check("A/B listening tip", len(advice.listen_for.split()) >= 5, advice.listen_for),
    ]
    if case.expect_any_device:
        checks.append(
            Check(
                "uses an expected device",
                any(d in case.expect_any_device for d in devices),
                f"used {devices}, expected one of {case.expect_any_device}",
            )
        )
    if case.expect_on_track_any:
        used = names(s for s in advice.steps if s.on_track)
        checks.append(
            Check(
                "works with what's on the track",
                any(d in case.expect_on_track_any for d in used),
                f"adjusted {used}, expected one of {case.expect_on_track_any}",
            )
        )
    if case.forbid_devices:
        bad = [d for d in devices if d in case.forbid_devices]
        checks.append(Check("avoids wrong devices", not bad, f"used {bad}"))
    return checks + _mention_check(case, advice)


def _mention_check(case: Case, advice: Advice) -> list[Check]:
    if not case.mention_any:
        return []
    text = _text(advice)
    return [
        Check(
            "mentions the key point",
            any(m.lower() in text for m in case.mention_any),
            f"none of {case.mention_any}",
        )
    ]


def chat_answer(case: Case, provider: Provider, base_dir: Path) -> Advice:
    """Run a case through the chat path and shape the answer like `ask()`'s for grading.

    The session is `session` (default: just `fixture`), with `fixture` selected and
    `pinned` pinned. A case with no fixture is a general chat with nothing in context.
    `prior_*` adds a scripted earlier turn, and `fixture_after` is the selected track as
    the user left it after that turn (for the refine diff).
    """
    def load(rel: str) -> TrackState:
        return TrackState.load(base_dir / rel)

    chat = Chat(context=ContextSet(follow_selection=bool(case.fixture)))
    live = None
    if case.fixture:
        paths = case.session or [case.fixture]
        if case.fixture not in paths:
            paths = [case.fixture, *paths]
        states = [load(p) for p in paths]
        for i, st in enumerate(states):
            if st.track.index is None:
                st.track.index = i
        index_of = {p: st.track.index for p, st in zip(paths, states)}
        arrangement = _arrangement(case, paths, states) if case.plays is not None else None
        live = StaticLive(states, selected=index_of[case.fixture], arrangement=arrangement)
        chat.context.pinned = [index_of[p] for p in case.pinned]
    engine = ChatEngine(provider)
    if case.prior_request:
        selected = {str(i): s for i, s in live.states.items() if i == live.selected} if live else {}
        chat.turns += [
            UserTurn(text=case.prior_request),
            AssistantTurn(text=case.prior_answer, snapshots=selected,
                          steps=[Step.model_validate(s) for s in case.prior_steps]),
        ]
        chat.title = "Earlier turn"
        if case.fixture_after:
            after = load(case.fixture_after)
            after.track.index = live.selected
            live.states[live.selected] = after
    for event in engine.send(chat, case.request, live):
        if isinstance(event, TurnError):
            raise ProviderError(event.message)
    turn = chat.turns[-1]
    return Advice(
        interpretation=turn.text,
        steps=turn.steps,
        listen_for=turn.listen_for or "",
        caution=turn.caution,
        warnings=turn.warnings,
    )


def _arrangement(case: Case, paths: list[str], states: list[TrackState]) -> Arrangement:
    """A 4/4 arrangement from the case's `plays` and `locators`, in bars."""
    beats = 4.0
    tracks = []
    for path, st in zip(paths, states):
        is_group = st.track.type == "group"
        clips = None if is_group else [
            {"start": (first - 1) * beats, "end": last * beats}
            for first, last in case.plays.get(path, [])
        ]
        tracks.append({"index": st.track.index, "is_group": is_group, "clips": clips})
    return Arrangement.model_validate({
        "signature": [4, 4],
        "locators": [{"name": c["name"], "time": (c["bar"] - 1) * beats} for c in case.locators],
        "tracks": tracks,
    })


def run_cases(
    cases: list[Case],
    provider: Provider,
    base_dir: Path,
    report_dir: str | Path | None = None,
    echo=print,
    chat: bool = False,
) -> list[Result]:
    results = []
    for case in cases:
        if case.chat_only and not chat:
            continue
        try:
            if chat:
                advice = chat_answer(case, provider, base_dir)
            else:
                advice = ask(TrackState.load(base_dir / case.fixture), case.request, provider)
            result = Result(case, grade(case, advice), advice)
        except ProviderError as e:
            result = Result(case, [], error=str(e))
        results.append(result)
        mark = "PASS" if result.passed else "FAIL"
        echo(f"{mark}  {case.id}: \"{case.request}\" on {case.fixture or 'no track'}")
        if result.error:
            echo(f"      error: {result.error}")
        for c in result.checks:
            if not c.passed:
                echo(f"      x {c.name}: {c.detail}")
    if report_dir:
        _save_report(results, Path(report_dir), provider, "-chat" if chat else "")
    return results


def _save_report(
    results: list[Result], report_dir: Path, provider: Provider, suffix: str = ""
) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    model = getattr(provider, "model", "")
    out = report_dir / f"{provider.name}-{model or 'default'}{suffix}.json".replace("/", "_")
    out.write_text(
        json.dumps(
            [
                {
                    "id": r.case.id,
                    "request": r.case.request,
                    "fixture": r.case.fixture,
                    "passed": r.passed,
                    "error": r.error,
                    "checks": [c.__dict__ for c in r.checks],
                    "advice": r.advice.model_dump() if r.advice else None,
                }
                for r in results
            ],
            indent=2,
        )
    )
