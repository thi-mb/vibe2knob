"""Print the selected track's state in Live.

    python3 -m live_bridge            # readable summary, then JSON
    python3 -m live_bridge --json     # JSON only, for piping into a file
    python3 -m live_bridge --summary  # every track, light, with its size and read time
    python3 -m live_bridge --arrangement  # where clips sit in Arrangement view, with read time
    python3 -m live_bridge --session  # Session view's clip grid and what plays, with read time
    python3 -m live_bridge --watch    # print connection and selection changes until Ctrl-C
"""

import argparse
import json
import sys
import time

from .client import LiveBridge, LiveError


def _summary(state):
    track = state["track"]
    mixer = track["mixer"]
    lines = [
        f"Track {track['index']}: {track['name']} ({track['type']})",
        f"  volume {mixer['volume']:.3f}  pan {mixer['panning']:+.2f}"
        f"  mute {mixer['mute']}  solo {mixer['solo']}  arm {mixer['arm']}",
    ]
    if not track["devices"]:
        lines.append("  (no devices)")
    for device in track["devices"]:
        rack = ", rack" if device["is_rack"] else ""
        lines.append(f"  [{device['index']}] {device['name']} <{device['class_name']}, {device['type']}{rack}>")
        for p in device["parameters"]:
            shown = p["display"] if p["display"] is not None else f"{p['value']:.3f}"
            lines.append(f"      {p['name']}: {shown}")
    return "\n".join(lines)


def _print_summary(live):
    started = time.monotonic()
    summary = live.session_summary()
    elapsed = time.monotonic() - started
    text = json.dumps(summary, ensure_ascii=False)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(
        f"{len(summary['tracks'])} tracks, {len(text.encode())} bytes as compact JSON, "
        f"read in {elapsed:.2f} s",
        file=sys.stderr,
    )


def _print_arrangement(live):
    started = time.monotonic()
    arrangement = live.arrangement()
    elapsed = time.monotonic() - started
    if arrangement is None:
        print("This AbletonOSC doesn't report arrangement clips.", file=sys.stderr)
        return
    print(json.dumps(arrangement, indent=2, ensure_ascii=False))
    clips = sum(len(t["clips"] or []) for t in arrangement["tracks"])
    print(f"{clips} clips, {len(arrangement['locators'])} locators, read in {elapsed:.2f} s",
          file=sys.stderr)


def _print_session_view(live):
    started = time.monotonic()
    view = live.session_view()
    elapsed = time.monotonic() - started
    if view is None:
        print("This AbletonOSC doesn't report Session view clips.", file=sys.stderr)
        return
    print(json.dumps(view, indent=2, ensure_ascii=False))
    clips = sum(1 for t in view["tracks"] for s in t["slots"] if s is not None)
    playing = sum(1 for t in view["tracks"] if t["playing_slot"] is not None)
    print(f"{len(view['scenes'])} scenes, {clips} clips, {playing} tracks playing a clip, "
          f"transport {'playing' if view['is_playing'] else 'stopped'}, read in {elapsed:.2f} s",
          file=sys.stderr)


def _watch(live):
    def on_status(status, detail):
        print(f"{time.strftime('%H:%M:%S')} status {status}" + (f": {detail}" if detail else ""))

    def on_selection(index):
        print(f"{time.strftime('%H:%M:%S')} selected {index}")

    live.subscribe_status(on_status)
    live.subscribe_selection(on_selection)
    live.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print JSON only")
    parser.add_argument("--track", type=int, help="track index to read instead of the selected one")
    parser.add_argument("--summary", action="store_true", help="print the light session summary")
    parser.add_argument("--arrangement", action="store_true",
                        help="print where clips sit in Arrangement view")
    parser.add_argument("--session", action="store_true",
                        help="print Session view's clip grid and what plays from it")
    parser.add_argument("--watch", action="store_true", help="print status and selection changes")
    parser.add_argument("--no-poll", action="store_true",
                        help="with --watch, rely on Live's selection listener only")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--timeout", type=float, default=2.0, help="seconds to wait for Live")
    args = parser.parse_args(argv)

    try:
        with LiveBridge(args.host, args.timeout, poll_selection=not args.no_poll) as live:
            if args.watch:
                _watch(live)
                return 0
            live.ping()
            if args.summary:
                _print_summary(live)
                return 0
            if args.arrangement:
                _print_arrangement(live)
                return 0
            if args.session:
                _print_session_view(live)
                return 0
            if args.track is None:
                state = live.selected_track_state()
            else:
                state = live.track_state(args.track)
    except (LiveError, ConnectionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not args.json:
        print(_summary(state))
        print()
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
