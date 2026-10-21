"""The core: track state + subjective request -> structured advice."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from .advice import ADVICE_SCHEMA, Advice, Step
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
    """Cheap sanity checks on the model's steps against the real chain (see check_steps)."""
    warnings = check_steps(advice.steps, [state])
    if not advice.steps:
        warnings.append("No steps were suggested.")
    return warnings


def check_steps(
    steps: list[Step], states: list[TrackState], default: TrackState | None = None
) -> list[str]:
    """Check each step against the track it names. A step naming no track is on `default`
    (the one track the chat looks at in full), or else on any track in context.

    Also fills each step's `stock_device`, `track` and `track_index`, and normalises `device` to the name
    shown on the chain (or the stock name for a new device).
    """
    warnings = []
    # Stock names of what is on each track, plus anything earlier steps add.
    present_by_target: dict[tuple, set[str]] = {}
    for i, step in enumerate(steps, 1):
        targets, problem = _target_tracks(step, states, default)
        if problem:
            warnings.append(f"Step {i}: {problem}")
        if len(targets) == 1:
            step.track = targets[0].track.name
            step.track_index = targets[0].track.index
        by_name = {d.name: d for s in targets for d in s.all_devices()}
        key = tuple(id(s) for s in targets)
        if key not in present_by_target:
            present_by_target[key] = {
                canonical(d.class_name) or canonical(d.name) for d in by_name.values()
            } - {None}
        present = present_by_target[key]
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
            # Models often read on_track as "goes on this track"; a new device never is.
            step.on_track = False
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
    return warnings


def _target_tracks(
    step: Step, states: list[TrackState], default: TrackState | None
) -> tuple[list[TrackState], str | None]:
    """The track(s) a step is on, and what's wrong with its track name, if anything."""
    if len(states) == 1:
        return states, None
    if not step.track:
        if default:
            return [default], None
        if not states:
            return [], None
        return states, "doesn't say which track it's on."
    wanted = step.track.strip().strip('"').casefold()
    named = [s for s in states if s.track.name.casefold() == wanted]
    if len(named) == 1:
        return named, None
    if named:
        return named, f"'{step.track}' matches more than one track."
    return states, f"names the track '{step.track}', which isn't in the chat."


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
