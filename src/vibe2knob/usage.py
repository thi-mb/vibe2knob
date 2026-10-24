"""Token usage: one record per model call, kept in a local ledger, and the reports built from it.

The ledger is usage.jsonl in the hub's home folder, one JSON record per line. It holds counts
only, never chat text, and nothing in it leaves the machine. The Usage section in Settings
reads it, so closed chats, chat titles and the one-shot `ask` path all count.

Providers report totals: uncached input, cache reads and writes, output, and (Claude) the
thinking inside output. `split_parts` divides them into the parts the panel shows. The
instructions and the tool definitions can be counted exactly (Claude's count_tokens), and
thinking is reported; everything else is an estimate by text length.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Literal

from pydantic import BaseModel, Field, ValidationError

from . import pricing
from .protocol import Usage, UsageChat, UsageDay, UsageGroup, UsageRange, UsageReport

log = logging.getLogger("vibe2knob.usage")

USAGE_SCHEMA_VERSION = 1

Kind = Literal["chat", "title", "ask", "eval"]
INPUT_PARTS = ("instructions", "tools", "live", "history", "message")
OUTPUT_PARTS = ("thinking", "answer", "steps")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UsageRecord(BaseModel):
    """One model call."""

    schema_version: Literal[1] = USAGE_SCHEMA_VERSION
    at: str = Field(default_factory=_now)
    kind: Kind = "chat"
    provider: str | None = None
    model: str | None = None
    chat_id: str | None = None
    turn_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    thinking_tokens: int = 0
    rounds: int = Field(default=1, description="Model calls summed into this record.")
    parts: dict[str, int] = Field(default_factory=dict)
    backfilled: bool = Field(default=False, description="Rebuilt from an old chat file.")

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    def cost(self) -> float | None:
        split = self.cost_split()
        return None if split is None else split[0] + split[1]

    def cost_split(self) -> tuple[float, float] | None:
        """(prompt, output) cost."""
        return pricing.cost_split(self.model, self.input_tokens, self.output_tokens,
                                  self.cache_read_tokens, self.cache_write_tokens)

    def as_usage(self) -> Usage:
        return total([self])

    @classmethod
    def from_raw(cls, raw: dict, **fields) -> "UsageRecord":
        """From a provider's usage dict (claude.py's `_usage`, or Ollama's two counts)."""
        fields["model"] = raw.get("model") or fields.get("model")
        return cls(
            input_tokens=raw.get("input_tokens") or 0,
            output_tokens=raw.get("output_tokens") or 0,
            cache_read_tokens=raw.get("cache_read_input_tokens") or 0,
            cache_write_tokens=raw.get("cache_creation_input_tokens") or 0,
            thinking_tokens=raw.get("thinking_tokens") or 0,
            **fields,
        )


def total(records: Iterable[UsageRecord]) -> Usage:
    """The sum of some records, priced. The model is set only if they all share one."""
    u = Usage()
    parts: dict[str, int] = defaultdict(int)
    models = set()
    prompt_cost = output_cost = 0.0
    priced = False
    for r in records:
        u.input_tokens += r.input_tokens
        u.output_tokens += r.output_tokens
        u.cache_read_tokens += r.cache_read_tokens
        u.cache_write_tokens += r.cache_write_tokens
        u.thinking_tokens += r.thinking_tokens
        u.calls += 1
        for k, v in r.parts.items():
            parts[k] += v
        models.add(r.model)
        c = r.cost_split()
        if c is None:
            if r.prompt_tokens or r.output_tokens:
                u.cost_complete = False
        else:
            prompt_cost += c[0]
            output_cost += c[1]
            priced = True
    u.parts = {k: v for k, v in parts.items() if k in INPUT_PARTS + OUTPUT_PARTS}
    u.model = next(iter(models)) if len(models) == 1 else None
    if priced:
        u.cost_usd = round(prompt_cost + output_cost, 6)
        u.prompt_cost_usd = round(prompt_cost, 6)
        u.output_cost_usd = round(output_cost, 6)
    return u


# Splitting a call into parts


@dataclass
class Sizes:
    """How long each part of one chat call was, in characters, for the estimated split."""

    instructions: int = 0
    tools: int = 0
    live: int = 0
    history: int = 0
    message: int = 0
    answer: int = 0
    steps: int = 0
    # Exact token counts, when the provider could count them.
    exact: dict[str, int] = field(default_factory=dict)


def split_parts(record: UsageRecord, sizes: Sizes) -> dict[str, int]:
    """The call's prompt and answer split into parts. The parts add up to the totals."""
    parts: dict[str, int] = {}
    prompt = record.prompt_tokens
    exact = {k: v for k, v in sizes.exact.items() if k in ("instructions", "tools")}
    if exact and sum(exact.values()) <= prompt:
        parts.update(exact)
        rest = prompt - sum(exact.values())
        parts.update(_share(rest, {k: getattr(sizes, k) for k in ("live", "history", "message")},
                            "message"))
    else:
        parts.update(_share(prompt, {k: getattr(sizes, k) for k in INPUT_PARTS}, "message"))
    thinking = min(record.thinking_tokens, record.output_tokens)
    parts["thinking"] = thinking
    parts.update(_share(record.output_tokens - thinking,
                        {"answer": sizes.answer, "steps": sizes.steps}, "answer"))
    return {k: v for k, v in parts.items() if v}


def _share(tokens: int, weights: dict[str, int], fallback: str) -> dict[str, int]:
    """Split `tokens` in proportion to `weights`, in whole tokens that add up exactly.

    With nothing to go on, it all goes on `fallback`.
    """
    weight = sum(weights.values())
    if tokens <= 0:
        return {}
    if weight <= 0:
        return {fallback: tokens}
    raw = {k: tokens * w / weight for k, w in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    left = tokens - sum(out.values())
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:left]:
        out[k] += 1
    return out


# The ledger


class Ledger:
    """usage.jsonl: appended to, never rewritten. A line that can't be read is skipped."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def append(self, records: Iterable[UsageRecord]) -> None:
        lines = "".join(r.model_dump_json(exclude_defaults=False) + "\n" for r in records)
        if not lines:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(lines)

    def read(self) -> list[UsageRecord]:
        if not self.path.exists():
            return []
        out = []
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if data.get("schema_version", 1) > USAGE_SCHEMA_VERSION:
                    continue  # written by a newer vibe2knob
                out.append(UsageRecord.model_validate(migrate(data)))
            except (ValueError, ValidationError):
                log.warning("Skipped line %d of %s", n, self.path)
        return out


# One function per old schema_version, each upgrading a dict to the next version.
MIGRATIONS: dict = {}


def migrate(data: dict) -> dict:
    version = data.get("schema_version", 1)
    while version < USAGE_SCHEMA_VERSION:
        data = MIGRATIONS[version](data)
        version = data["schema_version"]
    return data


def backfill(chats, default_model: str | None, provider: str | None) -> list[UsageRecord]:
    """Records for the answers in chats saved before the ledger existed.

    Old turns don't say which model answered, so the model in Settings is assumed.
    """
    records = []
    for chat in chats:
        for turn in chat.turns:
            usage = getattr(turn, "usage", None)
            if turn.role != "assistant" or not usage:
                continue
            records.append(UsageRecord.from_raw(
                usage, model=default_model, provider=provider, chat_id=chat.id,
                turn_id=turn.id, at=turn.created_at, backfilled=True,
            ))
    return records


# Reports

RANGE_DAYS: dict[str, int | None] = {"today": 1, "7d": 7, "30d": 30, "all": None}


def _local_day(at: str) -> date:
    try:
        return datetime.fromisoformat(at).astimezone().date()
    except ValueError:
        return date.min


def report(records: list[UsageRecord], range_: UsageRange, titles: dict[str, str | None],
           open_chats: set[str], today: date | None = None) -> UsageReport:
    """The usage.report for a range. `titles` names chats, open or closed."""
    today = today or datetime.now().astimezone().date()

    def since(days: int | None) -> date:
        return date.min if days is None else today - timedelta(days=days - 1)

    days_of = [(r, _local_day(r.at)) for r in records]
    totals = {name: total(r for r, d in days_of if d >= since(n))
              for name, n in RANGE_DAYS.items()}
    start = since(RANGE_DAYS[range_])
    chosen = [(r, d) for r, d in days_of if d >= start]
    if range_ == "all":
        start = min((d for _, d in chosen), default=today)
    by_day: dict[date, list[UsageRecord]] = defaultdict(list)
    for r, d in chosen:
        by_day[d].append(r)
    days = []
    d = start
    while d <= today:
        days.append(UsageDay(date=d.isoformat(), usage=total(by_day.get(d, []))))
        d += timedelta(days=1)
    in_range = [r for r, _ in chosen]
    by_chat: dict[str, list[UsageRecord]] = defaultdict(list)
    for r in in_range:
        if r.chat_id:
            by_chat[r.chat_id].append(r)
    chats = [UsageChat(chat_id=cid, title=titles.get(cid), open=cid in open_chats,
                       usage=total(rs)) for cid, rs in by_chat.items()]
    chats.sort(key=lambda c: (c.usage.cost_usd or 0, _tokens(c.usage)), reverse=True)
    t = totals[range_]
    prompt = t.input_tokens + t.cache_read_tokens + t.cache_write_tokens
    return UsageReport(
        range=range_,
        totals=totals,
        days=days,
        by_model=_groups(in_range, lambda r: r.model or "unknown"),
        by_kind=_groups(in_range, lambda r: r.kind),
        chats=chats,
        cache_hit_rate=round(t.cache_read_tokens / prompt, 4) if prompt else None,
        prices_checked_on=pricing.CHECKED_ON,
    )


def month_cost(records: Iterable[UsageRecord], today: date | None = None) -> float:
    """Estimated spend since the 1st of this month, local time. Unpriced calls count as 0."""
    today = today or datetime.now().astimezone().date()
    start = today.replace(day=1)
    return round(sum(r.cost() or 0.0 for r in records if start <= _local_day(r.at) <= today), 6)


def _tokens(u: Usage) -> int:
    return u.input_tokens + u.cache_read_tokens + u.cache_write_tokens + u.output_tokens


def _groups(records: list[UsageRecord], key) -> list[UsageGroup]:
    groups: dict[str, list[UsageRecord]] = defaultdict(list)
    for r in records:
        groups[key(r)].append(r)
    out = [UsageGroup(key=k, usage=total(rs)) for k, rs in groups.items()]
    out.sort(key=lambda g: (g.usage.cost_usd or 0, _tokens(g.usage)), reverse=True)
    return out
