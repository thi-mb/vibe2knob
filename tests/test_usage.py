from datetime import date

from vibe2knob import pricing
from vibe2knob.chat import AssistantTurn, Chat, ChatEngine, TurnDone, UserTurn
from vibe2knob.protocol import ContextSet
from vibe2knob.providers.fake import FakeProvider, Reply
from vibe2knob.usage import Ledger, Sizes, UsageRecord, backfill, report, split_parts, total


def rec(**kw):
    base = dict(model="claude-opus-5-5", input_tokens=1000, output_tokens=500)
    return UsageRecord(**{**base, **kw})


def test_cost_uses_each_token_kind():
    # Opus 5.5: $4 in, $5 cache write, $0.20 cache read, $20 out, per million.
    cost = pricing.cost_usd("claude-opus-5-5", 1_000_000, 1_000_000, 1_000_000, 1_000_000)
    assert cost == 4 + 20 + 0.20 + 5


def test_haiku_long_prompts_cost_more():
    short = pricing.cost_usd("claude-haiku-5-5", 100_000, 0, 0, 0)
    long = pricing.cost_usd("claude-haiku-5-5", 100_001, 0, 0, 0)
    assert round(short, 6) == 0.01 and round(long, 6) == round(100_001 * 0.5 / 1e6, 6)


def test_dated_ids_and_unknown_models():
    assert pricing.price_for("claude-haiku-4-5-20251001").name == "Claude Haiku 4.5"
    assert pricing.cost_usd("llama3.1", 10, 10, 0, 0) is None
    assert pricing.context_window("claude-haiku-4-5") == 200_000


def test_check_prices_reads_the_page_table():
    row = "| Claude Opus 5.5 | $4 / MTok | $5 / MTok | $8 / MTok | $0.20 / MTok<sup>2</sup> | $20 / MTok |"
    page = row + "\n" + "\n".join(
        f"| {p.name} | ${p.input} | ${p.cache_write} | $1 | ${p.cache_read} | ${p.output} |"
        for m, p in pricing.PRICES.items() if m != "claude-opus-5-5")
    assert pricing.check_prices(page) == []
    assert pricing.check_prices(page.replace("$20 / MTok", "$25 / MTok")) == [
        "Claude Opus 5.5 output: the page says $25.0, the table has $20.0"]
    assert "Claude Opus 5.5: not on the pricing page" in pricing.check_prices("")


def test_total_prices_and_flags_unknown_models():
    t = total([rec(), rec(model="llama3.1")])
    assert t.input_tokens == 2000 and t.model is None
    assert t.cost_usd == round(rec().cost(), 6) and t.cost_complete is False
    assert total([]).cost_usd is None


def test_split_adds_up_and_uses_exact_counts():
    r = rec(input_tokens=100, cache_read_tokens=9000, cache_write_tokens=900,
            output_tokens=600, thinking_tokens=200)
    sizes = Sizes(live=3000, history=1000, message=0, answer=900, steps=300,
                  exact={"instructions": 7000, "tools": 500})
    parts = split_parts(r, sizes)
    assert parts["instructions"] == 7000 and parts["tools"] == 500 and parts["thinking"] == 200
    assert parts["live"] + parts["history"] == 10_000 - 7500
    assert parts["live"] == 3 * parts["history"]
    assert parts["answer"] + parts["steps"] == 400 and parts["answer"] == 300
    # Without exact counts everything is split by length, and still adds up.
    est = split_parts(r, Sizes(instructions=1, tools=1, live=1, history=1, message=1))
    assert sum(v for k, v in est.items() if k not in ("thinking", "answer", "steps")) == 10_000
    assert est["answer"] == 400  # no sizes for the answer: it all counts as answer


def test_ledger_round_trip_skips_bad_lines(tmp_path):
    ledger = Ledger(tmp_path / "usage.jsonl")
    assert ledger.read() == [] and not ledger.exists()
    ledger.append([rec(chat_id="a"), rec(kind="title", chat_id="a")])
    with ledger.path.open("a") as f:
        f.write("not json\n" + '{"schema_version": 99}\n')
    ledger.append([rec(kind="ask")])
    assert [r.kind for r in ledger.read()] == ["chat", "title", "ask"]


def test_backfill_from_old_chats():
    chat = Chat(turns=[UserTurn(text="hi"), AssistantTurn(text="yo", usage={
        "input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100})])
    records = backfill([chat], "claude-sonnet-5-5", "claude")
    assert len(records) == 1 and records[0].backfilled and records[0].cache_read_tokens == 100
    assert records[0].model == "claude-sonnet-5-5" and records[0].chat_id == chat.id


def test_report_ranges_days_and_groups():
    records = [
        rec(at="2026-10-22T10:00:00+00:00", chat_id="a", cache_read_tokens=3000),
        rec(at="2026-10-22T11:00:00+00:00", chat_id="a", kind="title", model="claude-haiku-5-5"),
        rec(at="2026-10-20T10:00:00+00:00", chat_id="b"),
        rec(at="2026-09-01T10:00:00+00:00", chat_id="c", kind="ask"),
    ]
    out = report(records, "7d", {"a": "Wide pad", "b": None}, {"a"}, today=date(2026, 10, 22))
    assert [d.date for d in out.days][0] == "2026-10-16" and len(out.days) == 7
    assert out.totals["all"].input_tokens == 4000 and out.totals["7d"].input_tokens == 3000
    assert out.totals["today"].input_tokens == 2000
    assert [c.chat_id for c in out.chats] == ["a", "b"]
    assert out.chats[0].title == "Wide pad" and out.chats[0].open and not out.chats[1].open
    assert {g.key for g in out.by_kind} == {"chat", "title"}
    assert out.by_model[0].key == "claude-opus-5-5"
    assert out.cache_hit_rate == round(3000 / 6000, 4)
    assert out.prices_checked_on == pricing.CHECKED_ON
    everything = report(records, "all", {}, set(), today=date(2026, 10, 22))
    assert everything.days[0].date == "2026-09-01" and len(everything.days) == 52


def test_engine_records_turn_and_title_usage():
    provider = FakeProvider([Reply.with_steps("Widen it.", [], "listen")], title="Wide")
    engine = ChatEngine(provider)
    chat = Chat(context=ContextSet(follow_selection=False))
    events = list(engine.send(chat, "make it wider", None))
    done = next(e for e in events if isinstance(e, TurnDone))
    assert done.record.model == "scripted" and done.record.kind == "chat"
    assert done.record.chat_id == chat.id and done.record.turn_id == chat.turns[1].id
    assert sum(done.record.parts.values()) == done.record.prompt_tokens + done.record.output_tokens
    assert chat.turns[1].parts == done.record.parts
    titled = events[-1]
    assert titled.record.kind == "title" and titled.record.chat_id == chat.id


def test_engine_uses_exact_counts_when_the_provider_has_them():
    provider = FakeProvider([Reply("Sure.")])
    provider.count_parts = lambda system, tools: {"instructions": 50, "tools": 5}
    engine = ChatEngine(provider)
    chat = Chat(context=ContextSet(follow_selection=False))
    done = next(e for e in engine.send(chat, "hi", None) if isinstance(e, TurnDone))
    assert done.record.parts["instructions"] == 50 and done.record.parts["tools"] == 5


def test_total_splits_cost_between_prompt_and_output():
    t = total([rec(cache_read_tokens=1000)])
    # Opus 5.5: 1000 in at $4/M + 1000 cache reads at $0.20/M; 500 out at $20/M.
    assert t.prompt_cost_usd == round((1000 * 4 + 1000 * 0.2) / 1e6, 6)
    assert t.output_cost_usd == round(500 * 20 / 1e6, 6)
    assert t.cost_usd == round(t.prompt_cost_usd + t.output_cost_usd, 6)
