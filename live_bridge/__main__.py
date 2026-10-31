"""Print the selected track's state in Live.

    python3 -m live_bridge            # readable summary, then JSON
    python3 -m live_bridge --json     # JSON only, for piping into a file
    python3 -m live_bridge --summary  # every track, light, with its size and read time
    python3 -m live_bridge --arrangement  # where clips sit in Arrangement view, with read time
    python3 -m live_bridge --session  # Session view's clip grid and what plays, with read time
    python3 -m live_bridge --watch    # print connection and selection changes until Ctrl-C
    python3 -m live_bridge --params 3 1      # track 3, device 1: every parameter, raw + display
    python3 -m live_bridge --set mute 3 on   # writes, read back (use a scratch set):
    python3 -m live_bridge --set solo 3 off
    python3 -m live_bridge --set volume 3 0.7          # raw 0..1 (0.85 is 0 dB)
    python3 -m live_bridge --set pan 3 -0.5            # raw -1..1
    python3 -m live_bridge --set send 3 0 0.4          # track 3, send A
    python3 -m live_bridge --set device_on 3 1 off     # track 3, device 1
    python3 -m live_bridge --set parameter 3 1 5 0.25  # track 3, device 1, parameter 5
    python3 -m live_bridge --check-writes    # does Live take writes?
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


_SET_USAGE = {
    "mute": "TRACK on|off", "solo": "TRACK on|off", "volume": "TRACK RAW", "pan": "TRACK RAW",
    "send": "TRACK SEND RAW", "device_on": "TRACK DEVICE on|off",
    "parameter": "TRACK DEVICE PARAMETER RAW",
}


def _on_off(text):
    if text.lower() in ("on", "1", "true", "yes"):
        return True
    if text.lower() in ("off", "0", "false", "no"):
        return False
    raise ValueError(f"expected on or off, not {text!r}")


def _set(live, words):
    """Run one --set write. Returns what Live reads back."""
    kind, args = words[0], words[1:]
    calls = {
        "mute": lambda t, v: live.set_mute(int(t), _on_off(v)),
        "solo": lambda t, v: live.set_solo(int(t), _on_off(v)),
        "volume": lambda t, v: live.set_volume(int(t), float(v)),
        "pan": lambda t, v: live.set_panning(int(t), float(v)),
        "send": lambda t, s, v: live.set_send(int(t), int(s), float(v)),
        "device_on": lambda t, d, v: live.set_device_on(int(t), int(d), _on_off(v)),
        "parameter": lambda t, d, p, v: live.set_parameter(int(t), int(d), int(p), float(v)),
    }
    if kind not in calls:
        raise ValueError(f"--set takes one of {', '.join(calls)}")
    if len(args) != len(_SET_USAGE[kind].split()):
        raise ValueError(f"usage: --set {kind} {_SET_USAGE[kind]}")
    return calls[kind](*args)


def _print_params(info):
    print(f"Track {info['track']}, device {info['device']}: {info['name']} <{info['class_name']}>")
    for p in info["parameters"]:
        kind = "stepped" if p["is_quantized"] else "continuous"
        print(f"  [{p['index']}] {p['name']}: {p['display']} (raw {p['value']:.4g}, "
              f"{p['min']:.4g} to {p['max']:.4g}, {kind})")


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
    parser.add_argument("--set", nargs="+", metavar="WORD",
                        help="write to Live and read back: " + "; ".join(
                            f"{k} {v}" for k, v in _SET_USAGE.items()))
    parser.add_argument("--params", nargs=2, type=int, metavar=("TRACK", "DEVICE"),
                        help="print a device's parameters with ranges and display strings")
    parser.add_argument("--check-writes", action="store_true", help="check that Live takes writes")
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
            if args.set:
                started = time.monotonic()
                result = _set(live, args.set)
                print(json.dumps(result, ensure_ascii=False))
                print(f"written and read back in {time.monotonic() - started:.3f} s",
                      file=sys.stderr)
                return 0
            if args.params:
                info = live.parameter_info(*args.params)
                if args.json:
                    print(json.dumps(info, indent=2, ensure_ascii=False))
                else:
                    _print_params(info)
                return 0
            if args.check_writes:
                ok = live.check_writes()
                print("Live takes writes." if ok else "Live didn't take a write.")
                return 0 if ok else 1
            if args.track is None:
                state = live.selected_track_state()
            else:
                state = live.track_state(args.track)
    except (LiveError, ConnectionError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not args.json:
        print(_summary(state))
        print()
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
