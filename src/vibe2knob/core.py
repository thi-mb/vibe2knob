"""The core: track state + subjective request -> structured advice."""

from __future__ import annotations

import json

from pydantic import ValidationError

from .advice import ADVICE_SCHEMA, Advice
from .knowledge import Term, load_terms, match_terms
from .prompt import build_system_prompt, build_user_prompt
from .providers import Provider, ProviderError
from .state import TrackState
from .stock_devices import canonical


def build_prompts(
    state: TrackState, request: str, terms: list[Term] | None = None
) -> tuple[str, str, list[Term]]:
    terms = terms if terms is not None else load_terms()
    matched = match_terms(request, terms)
    return build_system_prompt(terms), build_user_prompt(state, request, matched), matched


def ask(
    state: TrackState,
    request: str,
    provider: Provider,
    terms: list[Term] | None = None,
) -> Advice:
    system, user, _ = build_prompts(state, request, terms)
    raw = provider.generate(system, user, ADVICE_SCHEMA)
    try:
        advice = Advice.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as e:
        raise ProviderError(f"{provider.name} returned advice in an unexpected shape: {e}") from e
    advice.warnings = check_advice(advice, state)
    return advice


def check_advice(advice: Advice, state: TrackState) -> list[str]:
    """Cheap sanity checks on the model's steps against the real chain."""
    warnings = []
    on_chain = {canonical(d.class_name) or canonical(d.name) or d.name for d in state.all_devices()}
    on_chain |= {d.name for d in state.all_devices()}
    for i, step in enumerate(advice.steps, 1):
        name = canonical(step.device)
        if name is None:
            warnings.append(f"Step {i}: '{step.device}' is not a stock Live device.")
            continue
        step.device = name
        if step.on_track and name not in on_chain and step.device not in on_chain:
            warnings.append(f"Step {i}: says {name} is on the track, but it isn't.")
        elif step.action in ("adjust", "enable", "disable", "remove", "reorder") and name not in on_chain:
            warnings.append(f"Step {i}: asks to {step.action} {name}, which isn't on the track yet.")
    if not advice.steps:
        warnings.append("No steps were suggested.")
    return warnings
