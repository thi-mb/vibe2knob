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

from .advice import Advice
from .core import ask
from .providers import Provider, ProviderError
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
    if case.mention_any:
        text = _text(advice)
        checks.append(
            Check(
                "mentions the key point",
                any(m.lower() in text for m in case.mention_any),
                f"none of {case.mention_any}",
            )
        )
    return checks


def run_cases(
    cases: list[Case],
    provider: Provider,
    base_dir: Path,
    report_dir: str | Path | None = None,
    echo=print,
) -> list[Result]:
    results = []
    for case in cases:
        state = TrackState.load(base_dir / case.fixture)
        try:
            advice = ask(state, case.request, provider)
            result = Result(case, grade(case, advice), advice)
        except ProviderError as e:
            result = Result(case, [], error=str(e))
        results.append(result)
        mark = "PASS" if result.passed else "FAIL"
        echo(f"{mark}  {case.id}: \"{case.request}\" on {case.fixture}")
        if result.error:
            echo(f"      error: {result.error}")
        for c in result.checks:
            if not c.passed:
                echo(f"      x {c.name}: {c.detail}")
    if report_dir:
        _save_report(results, Path(report_dir), provider)
    return results


def _save_report(results: list[Result], report_dir: Path, provider: Provider) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    model = getattr(provider, "model", "")
    out = report_dir / f"{provider.name}-{model or 'default'}.json".replace("/", "_")
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
