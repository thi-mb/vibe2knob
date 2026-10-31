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
from typing import Callable

from .advice import Advice, Step
from .chat import AssistantTurn, Chat, ChatEngine, ChatTitled, TurnDone, TurnError, UserTurn
from .core import ask
from .protocol import ContextSet
from .providers import Provider, ProviderError
from .arrangement import Arrangement
from .session import StaticLive
from .session_view import SessionView
from .state import TrackState
from .usage import UsageRecord, total


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
    # Reads (chat path): track names the model must read with read_track, or must not.
    expect_reads_any: list[str] = field(default_factory=list)
    expect_reads_all: list[str] = field(default_factory=list)
    forbid_reads: list[str] = field(default_factory=list)
    # Read tools the model must call (read_arrangement, read_scenes), whatever it reads.
    expect_tools: list[str] = field(default_factory=list)
    # Session view, scenes numbered from 1: fixture -> [scene, ...]. Unset means the Live
    # link reports no Session view; {} means a grid with no clips.
    scenes: dict[str, list[int]] | None = None
    scene_names: list[str] = field(default_factory=list)
    playing: dict[str, int] = field(default_factory=dict)  # fixture -> scene playing now


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Result:
    """One run of a case."""

    case: Case
    checks: list[Check]
    advice: Advice | None = None
    error: str | None = None
    records: list[UsageRecord] = field(default_factory=list)  # the model calls it made
    reads: list[str] = field(default_factory=list)  # tracks the model read (chat path)
    tools: list[str] = field(default_factory=list)  # read tools it called, in order

    @property
    def passed(self) -> bool:
        return self.error is None and all(c.passed for c in self.checks)

    @property
    def rounds(self) -> int:
        return sum(r.rounds for r in self.records)


@dataclass
class CaseResult:
    """Every run of one case. It passes only if every run passed."""

    case: Case
    runs: list[Result]

    @property
    def passes(self) -> int:
        return sum(r.passed for r in self.runs)

    @property
    def pass_rate(self) -> float:
        return self.passes / len(self.runs) if self.runs else 0.0

    @property
    def passed(self) -> bool:
        return bool(self.runs) and self.passes == len(self.runs)


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
            # Turning a switch on or off (an EQ band, Bass Mono) has no range to give.
            all(s.value_range for s in advice.steps
                if s.parameter and s.action not in ("enable", "disable")),
            "a step names a parameter without a value range",
        ),
    ]
    if advice.steps or case.min_steps:  # min_steps = 0: steps are optional
        checks.append(
            Check("A/B listening tip", len(advice.listen_for.split()) >= 5, advice.listen_for)
        )
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


# Words for what the model is given, which the user never sees (chat path).
HIDDEN_WORDS = ("summary", "context block")


def grade_wording(advice: Advice) -> list[Check]:
    """The answer talks about the session as Live shows it, not about its own context."""
    text = _text(advice)
    found = [w for w in HIDDEN_WORDS if w in text]
    return [Check("talks about Live, not its context", not found, f"says {found}")]


def grade_reads(case: Case, reads: list[str], tools: list[str] = ()) -> list[Check]:
    """Checks on which tracks the model read in a chat turn, and which read tools it called."""
    checks = []
    if case.expect_tools:
        missing = [t for t in case.expect_tools if t not in tools]
        checks.append(Check("calls the read tool the ask needs", not missing,
                            f"called {list(tools) or 'no read tool'}, missing {missing}"))
    got = f"read {reads or 'nothing'}"
    if case.expect_reads_any:
        checks.append(Check("reads a track the ask depends on",
                            any(r in case.expect_reads_any for r in reads),
                            f"{got}, expected one of {case.expect_reads_any}"))
    if case.expect_reads_all:
        missing = [r for r in case.expect_reads_all if r not in reads]
        checks.append(Check("reads every track the ask names", not missing,
                            f"{got}, missing {missing}"))
    if case.forbid_reads:
        bad = [r for r in reads if r in case.forbid_reads]
        checks.append(Check("doesn't read for its own sake", not bad, got))
    return checks


def chat_answer(case: Case, provider: Provider, base_dir: Path,
                records: list[UsageRecord] | None = None,
                reads: list[str] | None = None, tools: list[str] | None = None) -> Advice:
    """Run a case through the chat path and shape the answer like `ask()`'s for grading.

    The session is `session` (default: just `fixture`), with `fixture` selected and
    `pinned` pinned. A case with no fixture is a general chat with nothing in context.
    `prior_*` adds a scripted earlier turn, and `fixture_after` is the selected track as
    the user left it after that turn (for the refine diff). The calls' usage is added to
    `records`, as kind "eval", the names of the tracks the model read to `reads`, and the
    read tools it called to `tools`.
    """
    def load(rel: str) -> TrackState:
        return TrackState.load(base_dir / rel)

    # A title saves the naming call, which the cases don't grade.
    chat = Chat(context=ContextSet(follow_selection=bool(case.fixture)), title="Eval")
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
        grid = _session_view(case, paths, states) if case.scenes is not None else None
        live = StaticLive(states, selected=index_of[case.fixture], arrangement=arrangement,
                          session_view=grid)
        chat.context.pinned = [index_of[p] for p in case.pinned]
    engine = ChatEngine(provider)
    if tools is not None:
        handle = engine.tools.handle

        def recording(name, payload, ctx):
            if engine.tools.kind(name) == "read":
                tools.append(name)
            return handle(name, payload, ctx)

        engine.tools.handle = recording
    if case.prior_request:
        selected = {str(i): s for i, s in live.states.items() if i == live.selected} if live else {}
        chat.turns += [
            UserTurn(text=case.prior_request),
            AssistantTurn(text=case.prior_answer, snapshots=selected,
                          steps=[Step.model_validate(s) for s in case.prior_steps]),
        ]
        if case.fixture_after:
            after = load(case.fixture_after)
            after.track.index = live.selected
            live.states[live.selected] = after
    error = None
    for event in engine.send(chat, case.request, live):
        if isinstance(event, TurnError):
            error = event.message
        elif isinstance(event, (TurnDone, ChatTitled)) and event.record and records is not None:
            records.append(event.record.model_copy(update={"kind": "eval", "chat_id": None}))
    if error:
        raise ProviderError(error)
    turn = chat.turns[-1]
    if reads is not None:
        reads += [t.name for t in turn.looked_at]
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


def _session_view(case: Case, paths: list[str], states: list[TrackState]) -> SessionView:
    """A Session view grid from the case's `scenes`, `scene_names` and `playing`."""
    count = max([len(case.scene_names)] + [n for v in case.scenes.values() for n in v] + [1])
    tracks = []
    for path, st in zip(paths, states):
        mine = set(case.scenes.get(path, []))
        playing = case.playing.get(path)
        tracks.append({
            "index": st.track.index,
            "is_group": st.track.type == "group",
            "slots": [{"name": st.track.name, "length": 16.0} if n in mine else None
                      for n in range(1, count + 1)],
            "playing_slot": playing - 1 if playing else None,
        })
    names = case.scene_names
    return SessionView.model_validate({
        "is_playing": bool(case.playing),
        "scenes": [{"index": i, "name": names[i] if i < len(names) else ""} for i in range(count)],
        "tracks": tracks,
    })


def run_cases(
    cases: list[Case],
    provider: Provider,
    base_dir: Path,
    report_dir: str | Path | None = None,
    echo=print,
    chat: bool = False,
    repeat: int = 1,
    record_usage: Callable[[list[UsageRecord]], None] | None = None,
) -> list[CaseResult]:
    """Run each case `repeat` times. `record_usage` gets each run's model calls as it ends."""
    results = []
    for case in cases:
        if case.chat_only and not chat:
            continue
        runs = []
        for _ in range(repeat):
            run = _run_once(case, provider, base_dir, chat)
            if record_usage and run.records:
                record_usage(run.records)
            runs.append(run)
        result = CaseResult(case, runs)
        results.append(result)
        _echo_result(result, repeat, echo)
    if report_dir:
        _save_report(results, Path(report_dir), provider, "-chat" if chat else "")
    return results


def _run_once(case: Case, provider: Provider, base_dir: Path, chat: bool) -> Result:
    records: list[UsageRecord] = []
    reads: list[str] = []
    tools: list[str] = []
    try:
        if chat:
            advice = chat_answer(case, provider, base_dir, records, reads, tools)
            checks = grade(case, advice) + grade_reads(case, reads, tools) + grade_wording(advice)
            return Result(case, checks, advice, records=records, reads=reads, tools=tools)
        else:
            if hasattr(provider, "last_usage"):
                provider.last_usage = None
            try:
                advice = ask(TrackState.load(base_dir / case.fixture), case.request, provider)
            finally:
                raw = getattr(provider, "last_usage", None)
                if raw:
                    records.append(UsageRecord.from_raw(
                        raw, kind="eval", provider=provider.name,
                        model=getattr(provider, "model", None)))
        return Result(case, grade(case, advice), advice, records=records)
    except ProviderError as e:
        return Result(case, [], error=str(e), records=records, reads=reads, tools=tools)


def _echo_result(result: CaseResult, repeat: int, echo) -> None:
    case = result.case
    mark = "PASS" if result.passed else "FAIL"
    if repeat > 1:
        mark += f" {result.passes}/{len(result.runs)}"
    echo(f"{mark}  {case.id}: \"{case.request}\" on {case.fixture or 'no track'}")
    for n, run in enumerate(result.runs, 1):
        prefix = f"      run {n}: " if repeat > 1 else "      "
        if run.error:
            echo(f"{prefix}error: {run.error}")
        for c in run.checks:
            if not c.passed:
                echo(f"{prefix}x {c.name}: {c.detail}")


# Reports


def _run_row(run: Result) -> dict:
    u = total(run.records)
    return {
        "passed": run.passed,
        "error": run.error,
        "checks": [c.__dict__ for c in run.checks],
        "advice": run.advice.model_dump() if run.advice else None,
        "rounds": run.rounds,
        "reads": run.reads,
        "tools": run.tools,
        "tokens": _tokens(u),
        "cost_usd": u.cost_usd,
    }


def _tokens(u) -> dict:
    return {"input": u.input_tokens, "output": u.output_tokens, "cache_read": u.cache_read_tokens,
            "cache_write": u.cache_write_tokens, "thinking": u.thinking_tokens}


def report_rows(results: list[CaseResult]) -> list[dict]:
    """The report: one row per case. The top-level fields are the first run's, as in reports
    from before `--repeat`, so old and new reports read the same way."""
    rows = []
    for r in results:
        runs = [_run_row(run) for run in r.runs]
        first = runs[0] if runs else {"error": None, "checks": [], "advice": None}
        u = total(rec for run in r.runs for rec in run.records)
        rows.append({
            "id": r.case.id,
            "request": r.case.request,
            "fixture": r.case.fixture,
            "passed": r.passed,
            "error": first["error"],
            "checks": first["checks"],
            "advice": first["advice"],
            "repeat": len(r.runs),
            "passes": r.passes,
            "pass_rate": round(r.pass_rate, 4),
            "rounds": sum(run["rounds"] for run in runs),
            "tokens": _tokens(u),
            "cost_usd": u.cost_usd,
            "runs": runs,
        })
    return rows


def _save_report(
    results: list[CaseResult], report_dir: Path, provider: Provider, suffix: str = ""
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    model = getattr(provider, "model", "")
    out = report_dir / f"{provider.name}-{model or 'default'}{suffix}.json".replace("/", "_")
    out.write_text(json.dumps(report_rows(results), indent=2))
    return out


def load_report(path: str | Path) -> list[dict]:
    """A saved report's rows, with `repeat`, `passes` and `pass_rate` filled in for old ones."""
    rows = json.loads(Path(path).read_text())
    for row in rows:
        if "pass_rate" not in row:
            row["repeat"], row["passes"] = 1, int(bool(row.get("passed")))
            row["pass_rate"] = float(row["passes"])
    return rows


def compare(old: list[dict], new: list[dict], echo=print) -> bool:
    """Print the cases whose pass rate dropped. False if, over the cases both reports ran, the
    summed pass rate dropped by more than one case's worth (a whole case failing every run)."""
    before = {r["id"]: r for r in old}
    after = {r["id"]: r for r in new}
    shared = [i for i in after if i in before]

    def rate(row: dict) -> str:
        return f"{row['passes']}/{row['repeat']}"

    def share(row: dict) -> float:  # exact, where the saved pass_rate is rounded
        return row["passes"] / row["repeat"]

    dropped = [i for i in shared if share(after[i]) < share(before[i]) - 1e-9]
    raised = [i for i in shared if share(after[i]) > share(before[i]) + 1e-9]
    if dropped:
        echo("Dropped:")
        for i in dropped:
            echo(f"  {i}: {rate(before[i])} -> {rate(after[i])}")
    if raised:
        echo("Improved:")
        for i in raised:
            echo(f"  {i}: {rate(before[i])} -> {rate(after[i])}")
    added = [i for i in after if i not in before]
    gone = [i for i in before if i not in after]
    if added:
        echo(f"Not in the old report (not compared): {', '.join(added)}")
    if gone:
        echo(f"Not run this time: {', '.join(gone)}")
    total_before = sum(share(before[i]) for i in shared)
    total_after = sum(share(after[i]) for i in shared)
    ok = total_before - total_after <= 1 + 1e-9
    echo(f"Pass rate over {len(shared)} shared cases: {total_before:.2f} -> {total_after:.2f} "
         f"({total_after - total_before:+.2f} cases). "
         + ("OK" if ok else "Dropped by more than one case."))
    return ok
