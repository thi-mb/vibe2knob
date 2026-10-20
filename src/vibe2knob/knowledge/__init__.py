"""The open term-to-technique map (terms.toml) and helpers to use it."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path


@dataclass
class ParamHint:
    device: str
    parameter: str
    range: str


@dataclass
class Technique:
    name: str
    devices: list[str]
    how: str
    params: list[ParamHint] = field(default_factory=list)
    best_on: list[str] = field(default_factory=list)
    avoid_on: list[str] = field(default_factory=list)


@dataclass
class Term:
    id: str
    words: list[str]
    meaning: str
    techniques: list[Technique]
    cautions: list[str] = field(default_factory=list)


def load_terms(path: str | Path | None = None) -> list[Term]:
    if path is None:
        text = resources.files(__package__).joinpath("terms.toml").read_text()
    else:
        text = Path(path).read_text()
    data = tomllib.loads(text)
    terms = []
    for t in data.get("term", []):
        techniques = [
            Technique(
                name=x["name"],
                devices=list(x.get("devices", [])),
                how=x["how"],
                params=[ParamHint(**p) for p in x.get("params", [])],
                best_on=list(x.get("best_on", [])),
                avoid_on=list(x.get("avoid_on", [])),
            )
            for x in t.get("technique", [])
        ]
        terms.append(
            Term(
                id=t["id"],
                words=list(t["words"]),
                meaning=t["meaning"],
                techniques=techniques,
                cautions=list(t.get("cautions", [])),
            )
        )
    return terms


def match_terms(request: str, terms: list[Term]) -> list[Term]:
    """Terms whose words appear in the request, in order of first appearance."""
    text = " " + re.sub(r"[^a-z0-9 /-]+", " ", request.lower()) + " "
    hits: list[tuple[int, Term]] = []
    for term in terms:
        positions = [
            m.start()
            for w in term.words
            for m in [re.search(r"(?<![a-z])" + re.escape(w.lower()) + r"(?![a-z])", text)]
            if m
        ]
        if positions:
            hits.append((min(positions), term))
    return [t for _, t in sorted(hits, key=lambda h: h[0])]


def render_term(term: Term) -> str:
    lines = [f'## "{term.id}" (people say: {", ".join(term.words)})', f"Meaning: {term.meaning}"]
    for c in term.cautions:
        lines.append(f"Caution: {c}")
    for t in term.techniques:
        lines.append(f"- Technique: {t.name} [{', '.join(t.devices)}]")
        lines.append(f"  How: {t.how}")
        for p in t.params:
            lines.append(f"  * {p.device} > {p.parameter}: {p.range}")
        if t.best_on:
            lines.append(f"  Best on: {'; '.join(t.best_on)}")
        if t.avoid_on:
            lines.append(f"  Avoid on: {'; '.join(t.avoid_on)}")
    return "\n".join(lines)


def render_map(terms: list[Term]) -> str:
    return "\n\n".join(render_term(t) for t in terms)
