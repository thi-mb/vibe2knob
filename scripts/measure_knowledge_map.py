"""Measure what the knowledge map costs in a chat, before deciding whether to trim it.

The map is the long tail of the chat system prompt, cached with it. This runs the same
three-turn chat on two sessions (a small 4-track one and Thiago's 39-track set) and prints,
per turn: the system prompt split (knowledge map vs the rest), cache writes vs cache reads,
and the share of the turn's cost the map takes.

    python3 scripts/measure_knowledge_map.py --model claude-haiku-5-5   # needs ANTHROPIC_API_KEY
    python3 scripts/measure_knowledge_map.py --fake                     # sizes only, no key

With a key, token counts are exact (count_tokens for the split, the API's usage for the
turns). With --fake, sizes are in characters and the turns are scripted, so only the
split means anything. Add --json for the raw numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from live_bridge.client import _clip_spans  # noqa: E402
from vibe2knob import pricing  # noqa: E402
from vibe2knob.arrangement import Arrangement  # noqa: E402
from vibe2knob.chat import AssistantTurn, Chat, ChatEngine  # noqa: E402
from vibe2knob.knowledge import load_terms, render_map  # noqa: E402
from vibe2knob.prompt import CHAT_SYSTEM_TEMPLATE, VERSION_NOTE  # noqa: E402
from vibe2knob.protocol import ContextSet  # noqa: E402
from vibe2knob.session import StaticLive  # noqa: E402
from vibe2knob.state import Device, Track, TrackState  # noqa: E402

FIXTURES = ROOT / "fixtures"
TURNS = [
    "make the vocal wider",
    "it got a bit muddy now, can you fix that?",
    "and bring it more up front",
]


def load(name: str) -> TrackState:
    return TrackState.load(FIXTURES / f"{name}.json")


def small_session() -> StaticLive:
    """Vocal (selected), bass, pad and drums: indexes 0, 1, 2 and 4 from the fixtures."""
    states = [load("lead_vocal"), load("sub_bass"), load("synth_pad"), load("drum_group")]
    return StaticLive(states, selected=0)


def big_session() -> StaticLive:
    """The 39-track set from the arrangement probe, with its clips. The probe has names
    only, so audio tracks get the vocal's devices and MIDI tracks the pad's; "chorus lead"
    is the lead vocal fixture (selected)."""
    probe = json.loads((FIXTURES / "arrangement" / "probe_live11.json").read_text())
    vocal, pad = load("lead_vocal"), load("synth_pad")
    states, tracks = [], []
    for r in probe["tracks"]:
        index, name, group = r["index"], r["name"][0], r["is_foldable"][0]
        if name == "chorus lead" and index == 2:
            state = vocal.model_copy(deep=True)
            state.track.index, state.track.name = index, name
        else:
            kind = "group" if group else ("midi" if r["has_midi_input"][0] else "audio")
            source = pad if kind == "midi" else vocal
            devices = [] if group else [Device(name=d.name) for d in source.track.devices]
            state = TrackState(track=Track(index=index, name=name, type=kind, devices=devices))
        states.append(state)
        clips = None if group else _clip_spans(
            r["arrangement_clips/name"], r["arrangement_clips/start_time"],
            r["arrangement_clips/length"])
        tracks.append({"index": index, "is_group": group, "is_grouped": r["is_grouped"][0],
                       "clips": clips})
    arrangement = Arrangement(song_length=probe["song"]["song_length"][0], signature=[4, 4],
                              tracks=tracks)
    return StaticLive(states, selected=2, arrangement=arrangement)


def system_split(engine: ChatEngine, provider) -> dict:
    """The system prompt split into the knowledge map and the rest, in characters and,
    when the provider can count, in tokens."""
    knowledge = render_map(engine.terms)
    rest = CHAT_SYSTEM_TEMPLATE.format(knowledge="", version_note=VERSION_NOTE)
    out = {"chars": {"system": len(engine.system), "map": len(knowledge),
                     "rest": len(engine.system) - len(knowledge)},
           "terms": len(engine.terms)}
    count = getattr(provider, "count_parts", None)
    if count is not None:
        whole = (count(engine.system, engine.tools.specs()) or {})
        without = (count(rest, engine.tools.specs()) or {})
        if whole and without:
            out["tokens"] = {"system": whole["instructions"],
                             "map": whole["instructions"] - without["instructions"],
                             "rest": without["instructions"], "tools": whole["tools"]}
    return out


def map_cost(turn: AssistantTurn, model: str | None, map_tokens: int) -> dict | None:
    """What the map cost in this turn, as an estimate from its usage.

    The map sits in the cached prefix, so in each round it is either written to the cache
    (the turn's first round, when the cache was cold), read from it, or, with no caching at
    all, billed as plain input.
    """
    price = pricing.price_for(model)
    u = turn.usage
    if price is None or not u:
        return None
    rounds = u.get("rounds", 1)
    write, read = u.get("cache_creation_input_tokens") or 0, u.get("cache_read_input_tokens") or 0
    if not write and not read:
        cost = map_tokens * rounds * price.input
    else:
        writes = 1 if write >= map_tokens else 0
        cost = map_tokens * (writes * price.cache_write + (rounds - writes) * price.cache_read)
    record = turn.usage_record(model=model)
    total = record.cost() or 0.0
    cost /= 1_000_000
    return {"map_usd": round(cost, 6), "turn_usd": round(total, 6),
            "share": round(cost / total, 3) if total else None}


def run(name: str, live: StaticLive, provider, engine: ChatEngine, split: dict) -> list[dict]:
    chat = Chat(context=ContextSet(follow_selection=True), title="Measure")
    rows = []
    map_tokens = split.get("tokens", {}).get("map")
    for n, text in enumerate(TURNS, 1):
        for _ in engine.send(chat, text, live):
            pass
        turn = chat.turns[-1]
        if turn.error:
            raise SystemExit(f"{name} turn {n} failed: {turn.error}")
        u = turn.usage
        row = {"session": name, "turn": n, "rounds": u.get("rounds", 1),
               "input": u.get("input_tokens", 0),
               "cache_write": u.get("cache_creation_input_tokens") or 0,
               "cache_read": u.get("cache_read_input_tokens") or 0,
               "output": u.get("output_tokens", 0), "parts": turn.parts,
               "reads": [t.name for t in turn.looked_at]}
        if map_tokens:
            row["cost"] = map_cost(turn, getattr(provider, "model", None), map_tokens)
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--fake", action="store_true", help="No key: sizes only, scripted turns.")
    ap.add_argument("--json", action="store_true", help="Print the raw numbers as JSON.")
    args = ap.parse_args()
    if args.fake:
        from vibe2knob.providers.fake import FakeProvider
        provider = FakeProvider()
    else:
        from vibe2knob.providers import get_provider
        provider = get_provider("claude", args.model)
    engine = ChatEngine(provider)
    split = system_split(engine, provider)
    rows = []
    for name, live in (("small (4 tracks)", small_session()), ("big (39 tracks)", big_session())):
        rows += run(name, live, provider, engine, split)
    if args.json:
        print(json.dumps({"model": getattr(provider, "model", None), "split": split,
                          "turns": rows}, indent=2))
        return 0
    c = split["chars"]
    print(f"System prompt: {c['system']:,} chars, knowledge map {c['map']:,} "
          f"({c['map'] / c['system']:.0%}, {split['terms']} terms), the rest {c['rest']:,}.")
    t = split.get("tokens")
    if t:
        print(f"In tokens: {t['system']:,}, map {t['map']:,} ({t['map'] / t['system']:.0%}), "
              f"rest {t['rest']:,}; tool definitions {t['tools']:,}.")
    print()
    print(f"{'session':<18} turn rounds   input  c.write  c.read  output   map $    turn $  share")
    for r in rows:
        cost = r.get("cost") or {}
        share = cost.get("share")
        print(f"{r['session']:<18} {r['turn']:>4} {r['rounds']:>6} {r['input']:>7,} "
              f"{r['cache_write']:>8,} {r['cache_read']:>7,} {r['output']:>7,} "
              f"{cost.get('map_usd', 0):>8.5f} {cost.get('turn_usd', 0):>9.5f} "
              f"{'' if share is None else f'{share:.0%}':>6}")
    if args.fake:
        print("\n(--fake: the turns are scripted, so only the split above is real.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
