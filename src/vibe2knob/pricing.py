"""Model prices and context windows, for the cost estimates in the panel.

Anthropic has no API that returns prices, so they live here. `vibe2knob prices --check`
compares this table with Anthropic's pricing page; run it before each release and update
CHECKED_ON. The hub itself never fetches prices: it only talks to the model API.

Costs are estimates. Real billing is in the Anthropic Console.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CHECKED_ON = "2026-10-22"
PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"
CONSOLE_URL = "https://platform.claude.com/usage"

@dataclass(frozen=True)
class Price:
    """US dollars per million tokens. Cache writes are to the 5-minute cache, which is the
    one the providers use."""

    name: str
    input: float
    cache_write: float
    cache_read: float
    output: float
    # Some models charge more for long prompts: above `long_from` prompt tokens, every
    # token of the call costs `long_factor` times as much.
    long_from: int | None = None
    long_factor: float = 1.0


PRICES: dict[str, Price] = {
    "claude-fable-5-1": Price("Claude Fable 5.1", 10.00, 12.50, 0.25, 50.00),
    "claude-fable-5": Price("Claude Fable 5", 10.00, 12.50, 1.00, 50.00),
    "claude-opus-5-5": Price("Claude Opus 5.5", 4.00, 5.00, 0.20, 20.00),
    "claude-opus-5": Price("Claude Opus 5", 5.00, 6.25, 0.50, 25.00),
    "claude-opus-4-8": Price("Claude Opus 4.8", 5.00, 6.25, 0.50, 25.00),
    "claude-opus-4-7": Price("Claude Opus 4.7", 5.00, 6.25, 0.50, 25.00),
    "claude-opus-4-6": Price("Claude Opus 4.6", 5.00, 6.25, 0.50, 25.00),
    "claude-sonnet-5-5": Price("Claude Sonnet 5.5", 2.00, 2.50, 0.10, 10.00),
    "claude-sonnet-5": Price("Claude Sonnet 5", 2.00, 2.50, 0.20, 10.00),
    "claude-sonnet-4-6": Price("Claude Sonnet 4.6", 3.00, 3.75, 0.30, 15.00),
    "claude-haiku-5-5": Price("Claude Haiku 5.5", 0.10, 0.125, 0.01, 0.50,
                              long_from=100_000, long_factor=5.0),
    "claude-haiku-4-5": Price("Claude Haiku 4.5", 1.00, 1.25, 0.10, 5.00),
}

# Context windows, for when the Models API can't be asked (no key, offline, Ollama).
CONTEXT_WINDOWS: dict[str, int] = {model: 1_000_000 for model in PRICES}
CONTEXT_WINDOWS["claude-haiku-4-5"] = 200_000


def price_for(model: str | None) -> Price | None:
    """The price of a model id, also for dated ids like claude-haiku-4-5-20251001."""
    if not model:
        return None
    if model in PRICES:
        return PRICES[model]
    base = re.sub(r"-\d{8}$", "", model)
    return PRICES.get(base)


def cost_split(model: str | None, input_tokens: int, output_tokens: int, cache_read: int,
               cache_write: int) -> tuple[float, float] | None:
    """The estimated cost of one call's prompt and of its output, or None with no known price."""
    p = price_for(model)
    if p is None:
        return None
    factor = 1.0
    if p.long_from is not None and input_tokens + cache_read + cache_write > p.long_from:
        factor = p.long_factor
    prompt = input_tokens * p.input + cache_write * p.cache_write + cache_read * p.cache_read
    return prompt * factor / 1_000_000, output_tokens * p.output * factor / 1_000_000


def cost_usd(model: str | None, input_tokens: int, output_tokens: int, cache_read: int,
             cache_write: int) -> float | None:
    """The estimated cost of one call, or None for a model with no known price."""
    split = cost_split(model, input_tokens, output_tokens, cache_read, cache_write)
    return None if split is None else split[0] + split[1]


def context_window(model: str | None) -> int | None:
    if not model:
        return None
    return CONTEXT_WINDOWS.get(model) or CONTEXT_WINDOWS.get(re.sub(r"-\d{8}$", "", model))


# The maintainer check


def check_prices(page: str) -> list[str]:
    """Compare the table with the text of Anthropic's pricing page. Returns the differences.

    For each model the page's first table row naming it is read. That table's columns are:
    input, 5-minute cache writes, 1-hour cache writes, cache hits, output.
    """
    problems = []
    for model, p in PRICES.items():
        row = next((line for line in page.splitlines()
                    if re.search(rf"\b{re.escape(p.name)}\b(?![\d.])", line)
                    and line.lstrip().startswith("|")), None)
        if row is None:
            problems.append(f"{p.name}: not on the pricing page")
            continue
        amounts = [float(a) for a in re.findall(r"\$(\d+(?:\.\d+)?)", row)]
        if len(amounts) < 5:
            problems.append(f"{p.name}: couldn't read its prices from: {row.strip()}")
            continue
        page_prices = {"input": amounts[0], "cache_write": amounts[1], "cache_read": amounts[3],
                       "output": amounts[4]}
        for field, value in page_prices.items():
            if value != getattr(p, field):
                problems.append(f"{p.name} {field}: the page says ${value}, "
                                f"the table has ${getattr(p, field)}")
    return problems
