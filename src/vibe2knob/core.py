"""The core: track state + subjective request -> structured advice."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from .advice import ADVICE_SCHEMA, Advice
from .knowledge import Term, load_terms, match_terms
from .prompt import build_system_prompt, build_user_prompt
from .providers import Provider, ProviderError
from .state import Device, TrackState
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
    """Cheap sanity checks on the model's steps against the real chain.

    Also fills each step's `stock_device`, and normalises `device` to the name
    shown on the chain (or the stock name for a new device).
    """
    warnings = []
    by_name = {d.name: d for d in state.all_devices()}
    # Stock names of what is on the track, plus anything earlier steps add.
    present = {canonical(d.class_name) or canonical(d.name) for d in by_name.values()} - {None}
    for i, step in enumerate(advice.steps, 1):
        device = _find_on_chain(step.device, by_name)
        if step.action == "add_device" and device is not None and device.is_plugin:
            warnings.append(f"Step {i}: adds {device.name}, which is not a stock Live device.")
            continue
        if device is not None:
            step.device = device.name
            stock = None if device.is_plugin else canonical(device.class_name) or canonical(device.name)
        else:
            stock = canonical(_strip_notes(step.device))
            if stock is None:
                warnings.append(f"Step {i}: '{step.device}' is not a stock Live device.")
                continue
            step.device = stock
        step.stock_device = stock
        if step.action == "add_device":
            if step.on_track:
                warnings.append(f"Step {i}: adds {step.device} but marks it as already on the track.")
            if stock:
                present.add(stock)
            continue
        if device is not None and device.is_plugin:
            continue  # adjusting a plugin that is already on the track is fine
        if device is None and stock not in present:
            if step.on_track:
                warnings.append(f"Step {i}: says {step.device} is on the track, but it isn't.")
            else:
                warnings.append(
                    f"Step {i}: asks to {step.action} {step.device}, which isn't on the track yet."
                )
    if not advice.steps:
        warnings.append("No steps were suggested.")
    return warnings


_NOTES = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")


def _strip_notes(name: str) -> str:
    """'Kick 909 (Simpler, Kick chain)' -> 'Kick 909'. Strips trailing (...) and [...]."""
    while True:
        stripped = _NOTES.sub("", name).strip()
        if stripped == name or not stripped:
            return name
        name = stripped


def _find_on_chain(name: str, by_name: dict[str, Device]) -> Device | None:
    """The chain device a step means, tolerating notes the model appends to the name."""
    candidate = name.strip()
    while True:
        if candidate in by_name:
            return by_name[candidate]
        stripped = _NOTES.sub("", candidate).strip()
        if stripped == candidate or not stripped:
            return None
        candidate = stripped
