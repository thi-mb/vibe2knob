"""Command line: vibe2knob ask "make this wider" --state track.json"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .advice import format_advice
from .core import ask, build_prompts
from .knowledge import load_terms
from .providers import PROVIDERS, ProviderError, get_provider
from .state import TrackState


def _read_state(path: str) -> TrackState:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    return TrackState.from_json(text)


def _record_usage(records) -> None:
    """Add calls from the terminal to the panel's usage ledger. Never fails the command."""
    from .store import default_home
    from .usage import Ledger

    try:
        Ledger(default_home() / "usage.jsonl").append(r for r in records if r is not None)
    except OSError:
        pass


def cmd_ask(args: argparse.Namespace) -> int:
    state = _read_state(args.state)
    terms = load_terms(args.terms)
    if args.dry_run:
        system, user, matched = build_prompts(state, args.request, terms)
        print("=== system ===\n" + system + "\n=== user ===\n" + user)
        return 0
    from .usage import UsageRecord

    provider = get_provider(args.provider, args.model)
    try:
        advice = ask(state, args.request, provider, terms)
    finally:
        if getattr(provider, "last_usage", None):
            _record_usage([UsageRecord.from_raw(
                provider.last_usage, kind="ask", provider=provider.name,
                model=getattr(provider, "model", None))])
    if args.json:
        print(advice.model_dump_json(indent=2))
    else:
        print(format_advice(advice))
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    from .advice import format_step
    from .chat import Chat, ChatEngine, ChatTitled, TurnDone, TurnError, TurnSteps, TurnText
    from .protocol import ContextSet
    from .session import StaticLive

    states = [_read_state(p) for p in args.state]
    live = StaticLive(states) if states else None
    if live:
        live.selected = next(iter(live.states))  # the first --state is the selected track
    chat = Chat(context=ContextSet(follow_selection=bool(states)))
    provider = get_provider(args.provider, args.model)
    engine = ChatEngine(provider, load_terms(args.terms))
    print("Chatting about: " + (", ".join(s.track.name for s in states) or "nothing (general chat)"))
    print("Ctrl-D to quit.\n")
    while True:
        try:
            text = input("> ").strip()
        except EOFError:
            print()
            break
        if not text:
            continue
        for event in engine.send(chat, text, live):
            if isinstance(event, TurnText):
                print(event.text, end="", flush=True)
            elif isinstance(event, TurnSteps):
                print("\n")
                for i, s in enumerate(event.steps, 1):
                    print(format_step(i, s) + f"\n   {s.explanation}")
                print(f"\nListen: {event.listen_for}")
                if event.caution:
                    print(f"Watch out: {event.caution}")
                for w in event.warnings:
                    print(f"[check] {w}")
            elif isinstance(event, TurnError):
                print(f"\nerror: {event.message}", file=sys.stderr)
            elif isinstance(event, ChatTitled):
                print(f"\n(topic: {event.title})", end="")
                _record_usage([event.record])
            elif isinstance(event, TurnDone):
                _record_usage([event.record])
        print("\n")
    if args.save:
        chat.save(args.save)
    return 0


def cmd_panel(args: argparse.Namespace) -> int:
    try:
        from .server import run_panel
    except ImportError as e:
        print(f"error: the panel needs more packages ({e.name}): pip install 'vibe2knob[panel]'",
              file=sys.stderr)
        return 2
    return run_panel(
        args.port,
        fake_live=args.fake or args.fake_live,
        fake_model=args.fake or args.fake_model,
        data_dir=args.data_dir,
        token=args.token,
        dev=args.dev,
        open_browser=not args.no_browser,
        window=args.window,
    )


def cmd_terms(args: argparse.Namespace) -> int:
    for t in load_terms(args.terms):
        print(f"{t.id:10} {', '.join(t.words)}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .evals import load_cases, run_cases

    cases = load_cases(args.cases)
    if args.only:
        cases = [c for c in cases if c.id in args.only]
    provider = get_provider(args.provider, args.model)
    results = run_cases(
        cases, provider, Path(args.cases).parent, report_dir=args.report_dir, chat=args.chat
    )
    failed = [r for r in results if not r.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} cases passed")
    return 1 if failed else 0


def cmd_prices(args: argparse.Namespace) -> int:
    """Show the price table, or with --check compare it with Anthropic's pricing page."""
    from . import pricing

    if not args.check:
        print(f"Prices in US$ per million tokens, checked on {pricing.CHECKED_ON}:")
        for model, p in pricing.PRICES.items():
            print(f"  {model:20} in {p.input:>6}  cache write {p.cache_write:>6}  "
                  f"cache read {p.cache_read:>5}  out {p.output:>6}")
        return 0
    import urllib.request

    request = urllib.request.Request(pricing.PRICING_URL, headers={"User-Agent": "vibe2knob"})
    with urllib.request.urlopen(request, timeout=30) as resp:
        page = resp.read().decode("utf-8", errors="replace")
    problems = pricing.check_prices(page)
    if not problems:
        print(f"The price table matches {pricing.PRICING_URL}. "
              f"Set CHECKED_ON in pricing.py to today if it's a release.")
        return 0
    print("The price table differs from the pricing page:")
    for problem in problems:
        print(f"  {problem}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibe2knob",
        description="Turn subjective asks into steps with Live's stock devices.",
    )
    parser.add_argument("--terms", help="Use a different knowledge map (TOML).")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_provider_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--provider", default="claude", choices=sorted(PROVIDERS))
        p.add_argument("--model", help="Override the provider's default model.")

    p = sub.add_parser("ask", help="Ask for advice on one track.")
    p.add_argument("request", help='What you want, e.g. "make this wider".')
    p.add_argument(
        "--state", "-s", required=True, help="Track-state JSON file, or - for stdin."
    )
    add_provider_args(p)
    p.add_argument("--json", action="store_true", help="Print the structured advice as JSON.")
    p.add_argument("--dry-run", action="store_true", help="Print the prompt, don't call a model.")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("chat", help="Chat about one or more tracks in the terminal.")
    p.add_argument(
        "--state", "-s", action="append", default=[],
        help="Track-state JSON file; repeat for more tracks. The first is the selected one.",
    )
    add_provider_args(p)
    p.add_argument("--save", help="Write the chat to this JSON file when you quit.")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("panel", help="Start the hub and open the panel in the browser, or in a window.")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--fake", action="store_true",
                   help="Fake Live (the fixtures) and a scripted model: no Live, no key.")
    p.add_argument("--fake-live", action="store_true", help="Use the fixtures instead of Live.")
    p.add_argument("--fake-model", action="store_true",
                   help="Use a scripted model; chats go to a temporary folder.")
    p.add_argument("--data-dir", help="Where chats and settings live "
                   "(default ~/Library/Application Support/vibe2knob, or $VIBE2KNOB_HOME).")
    p.add_argument("--token", help="Use this access token instead of a random one (for tests).")
    p.add_argument("--dev", action="store_true",
                   help="Also accept the Vite dev server (npm run dev) and open it instead.")
    p.add_argument("--no-browser", action="store_true", help="Don't open the browser.")
    p.add_argument("--window", action="store_true",
                   help="Open the panel in its own always-on-top window instead of the browser "
                   "(needs the [window] extra). Closing it stops the hub.")
    p.set_defaults(func=cmd_panel)

    p = sub.add_parser("terms", help="List the subjective terms in the knowledge map.")
    p.set_defaults(func=cmd_terms)

    p = sub.add_parser("prices", help="Show the price table behind the cost estimates.")
    p.add_argument("--check", action="store_true",
                   help="Compare it with Anthropic's pricing page (for maintainers, before a release).")
    p.set_defaults(func=cmd_prices)

    p = sub.add_parser("eval", help="Run the advice test cases against a model.")
    p.add_argument("--cases", default="evals/cases.toml")
    p.add_argument("--only", nargs="*", help="Case ids to run.")
    p.add_argument("--report-dir", default="evals/reports", help="Where to save full answers.")
    p.add_argument("--chat", action="store_true",
                   help="Run the cases through the chat path (streamed prose + suggest_steps).")
    add_provider_args(p)
    p.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ProviderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
