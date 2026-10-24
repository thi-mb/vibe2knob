"""Probe what AbletonOSC tells us about Session view. Read-only: it changes nothing in Live.

Run from the vibe2knob checkout, with Live open and AbletonOSC on:
    python3 scripts/probe_session_view.py > session_probe.json

It prints a readable summary to stderr and the raw replies as JSON to stdout. Each batch
is timed on its own, so an address that never answers shows up as a batch that waited
out its timeout.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live_bridge.osc import OscClient  # noqa: E402

TIMEOUT = 2.0
SONG = ["num_scenes", "num_tracks", "is_playing", "tempo"]
# Candidates for all scene names at once; not every AbletonOSC has them.
SCENE_LISTS = ["scenes/name"]
TRACK = ["name", "is_foldable", "is_grouped", "clips/name", "clips/length",
         "playing_slot_index", "fired_slot_index"]
SCRIPT_DIRS = [
    Path.home() / "Music/Ableton/User Library/Remote Scripts/AbletonOSC",
    Path.home() / "Music/Ableton/User Library/Remote Scripts/abletonosc",
]


def abletonosc_version():
    """Where AbletonOSC is installed and, if it's a git checkout, its commit."""
    for folder in SCRIPT_DIRS:
        if not folder.is_dir():
            continue
        info = {"path": str(folder)}
        try:
            info["commit"] = subprocess.run(
                ["git", "-C", str(folder), "log", "-1", "--format=%H %cs %s"],
                capture_output=True, text=True, timeout=5).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            info["commit"] = None
        newest = max((p.stat().st_mtime for p in folder.rglob("*.py")), default=None)
        info["newest_py_mtime"] = time.strftime("%Y-%m-%d", time.localtime(newest)) if newest else None
        return info
    return {"path": None}


def timed(osc, requests, key_len, timeout=TIMEOUT):
    started = time.monotonic()
    replies = osc.request_many(requests, key_len, timeout)
    return replies, round(time.monotonic() - started, 3)


def plain(value):
    return list(value) if value is not None else None


def main():
    out = {"abletonosc": abletonosc_version(), "batches": {}, "missing": []}
    with OscClient() as osc:
        if osc.request("/live/test") is None:
            sys.exit("No answer from AbletonOSC on port 11000.")
        version = osc.request("/live/application/get/version")
        out["live_version"] = plain(version)

        song, secs = timed(osc, [(f"/live/song/get/{p}", []) for p in SONG], {})
        out["batches"]["song"] = secs
        out["song"] = {p: plain(song.get((f"/live/song/get/{p}",))) for p in SONG}
        num_tracks = int(song[("/live/song/get/num_tracks",)][0])
        num_scenes = int(song[("/live/song/get/num_scenes",)][0])

        out["scene_lists"] = {}
        for p in SCENE_LISTS:
            reply, secs = timed(osc, [(f"/live/song/get/{p}", [])], {})
            out["batches"][f"song {p}"] = secs
            out["scene_lists"][p] = plain(reply.get((f"/live/song/get/{p}",)))

        # One scene at a time, as a fallback for the list above.
        addr = "/live/scene/get/name"
        reply, secs = timed(osc, [(addr, [s]) for s in range(num_scenes)], {addr: 1})
        out["batches"]["scene/get/name per scene"] = secs
        out["scenes"] = [plain(reply.get((addr, s))) for s in range(num_scenes)]

        # One batch per track property, so each address gets its own timing.
        rows = [{"index": t} for t in range(num_tracks)]
        for p in TRACK:
            addr = f"/live/track/get/{p}"
            reply, secs = timed(osc, [(addr, [t]) for t in range(num_tracks)], {addr: 1})
            out["batches"][f"track {p}"] = secs
            for t in range(num_tracks):
                value = reply.get((addr, t))
                if value is None:
                    out["missing"].append(f"track {t} {p}")
                rows[t][p] = plain(value)
        out["tracks"] = rows

        # The same per-track reads skipping group tracks, to see what groups cost.
        groups = {r["index"] for r in rows if r["is_foldable"] and r["is_foldable"][0]}
        reqs = [(f"/live/track/get/{p}", [t]) for t in range(num_tracks) if t not in groups
                for p in ("clips/name", "clips/length", "playing_slot_index", "fired_slot_index")]
        _, secs = timed(osc, reqs, {a: 1 for a, _ in reqs})
        out["batches"]["all clip reads, groups skipped"] = secs
        reqs = [(f"/live/track/get/{p}", [t]) for t in range(num_tracks)
                for p in ("clips/name", "clips/length", "playing_slot_index", "fired_slot_index")]
        _, secs = timed(osc, reqs, {a: 1 for a, _ in reqs})
        out["batches"]["all clip reads, every track"] = secs

    print(json.dumps(out, indent=2, ensure_ascii=False))

    print(f"Live {out['live_version']}, AbletonOSC {out['abletonosc']}", file=sys.stderr)
    print(f"song: {json.dumps(out['song'])}", file=sys.stderr)
    print(f"scene lists: {json.dumps(out['scene_lists'], ensure_ascii=False)[:300]}", file=sys.stderr)
    print(f"scenes: {json.dumps(out['scenes'], ensure_ascii=False)[:300]}", file=sys.stderr)
    for name, secs in out["batches"].items():
        print(f"  {secs:6.2f} s  {name}", file=sys.stderr)
    for row in rows:
        name = (row["name"] or ["?"])[0]
        clips = sum(1 for c in (row["clips/name"] or []) if c is not None)
        kind = " (group)" if row["index"] in groups else ""
        print(f"  [{row['index']}] {name}{kind}: {clips} clips, playing "
              f"{plain(row['playing_slot_index'])}, fired {plain(row['fired_slot_index'])}",
              file=sys.stderr)
    if out["missing"]:
        print(f"no reply for {len(out['missing'])} reads, e.g. {out['missing'][:5]}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
