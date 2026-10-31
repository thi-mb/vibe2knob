"""Probe how Live answers writes through AbletonOSC. This CHANGES Live: use a scratch set.

It measures what the Do mode setters need: which values each write takes, whether a
read straight after a write sees the new value, how long a write takes, how display
strings ("2.50 kHz", "-6.0 dB") follow the raw value, and how many undo steps a write
leaves in Live's history. Everything it changes is set back at the end of each test.

Prepare a scratch copy of a set with at least: one audio track, one MIDI track and one
group track (each with a device on it), a return track (for send 0), and these stock
devices at the top level of any track: EQ Eight, Compressor, Utility, Reverb, Auto Filter.
Close `vibe2knob panel` first (only one AbletonOSC client can hold UDP 11001), and keep
the transport stopped for the main probe: it sweeps gains and volumes across their range.

Run from the vibe2knob checkout, with Live open and AbletonOSC on:

    python3 scripts/probe_writes.py > fixtures/writes/probe_live11.json
        the main probe; a readable summary goes to stderr

    python3 scripts/probe_writes.py --listen
        while a clip plays on the Auto Filter's track: three runs of a 5-set correction
        loop on the filter frequency, 2 s apart, then the value is set back. Listen for
        zipper noise or clicks.

    python3 scripts/probe_writes.py --mixer-spread
    python3 scripts/probe_writes.py --mixer-restore
        AbletonOSC can't give display strings for mixer volume or pan. --mixer-spread sets
        the first regular tracks' volume and pan to a known raw value each (and saves the
        old values), so one look at Session view's mixer (or a screenshot) gives the dB and
        pan text for each. --mixer-restore puts the old values back.
"""

import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live_bridge.osc import OscClient  # noqa: E402

TIMEOUT = 2.0
# Host, send port, receive port; --ports changes the last two (for a test server).
PORTS = ["127.0.0.1", 11000, 11001]
SCRIPT_DIRS = [
    Path.home() / "Music/Ableton/User Library/Remote Scripts/AbletonOSC",
    Path.home() / "Music/Ableton/User Library/Remote Scripts/abletonosc",
]
RESTORE_FILE = Path(__file__).resolve().parent.parent / "fixtures/writes/.mixer-restore.json"

KEY_LEN = {
    "/live/track/get/send": 2,
    "/live/device/get/parameter/value": 3,
    "/live/device/get/parameter/value_string": 3,
}
for _p in ["name", "is_foldable", "has_midi_input", "mute", "solo", "volume", "panning",
           "devices/name", "devices/class_name"]:
    KEY_LEN[f"/live/track/get/{_p}"] = 1
for _p in ["parameters/name", "parameters/value", "parameters/min", "parameters/max",
           "parameters/is_quantized"]:
    KEY_LEN[f"/live/device/get/{_p}"] = 2

# Stock devices by class name, with the parameters the brief asks for first. Names are
# matched exactly (case-insensitive), then by "contains".
DEVICES = {
    "Eq8": ["1 Frequency A", "1 Gain A"],
    "Compressor2": ["Threshold", "Ratio"],
    "StereoGain": ["Stereo Width", "Gain"],
    "Reverb": ["Decay Time", "Dry/Wet"],
    "AutoFilter": ["Frequency"],
}
KEY_POINTS = 41      # points across the range for the parameters named above
OTHER_POINTS = 11    # for every other continuous parameter on those devices
MAX_STEPS = 128      # quantized parameters with up to this many steps get every step

VOLUME_POINTS = [1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05, 0.0]
PAN_POINTS = [-1.0, -0.5, -0.25, -0.02, 0.0, 0.02, 0.25, 0.5, 1.0]


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def abletonosc_version():
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
        return info
    return {"path": None}


def points(lo, hi, n):
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


class Wire:
    """OSC with two ways to ask: keyed round trips, and ordered pipelines.

    A pipeline sends many messages at once (Live handles them in one 100 ms tick, in
    order) and collects replies per address in arrival order, so several replies with the
    same ids (one parameter read after each of several sets) can be told apart.
    """

    def __init__(self, osc):
        self.osc = osc
        self.cond = threading.Condition()
        self.inbox = defaultdict(list)
        self.listening = set()
        self.errors = []
        osc.subscribe("/live/error", lambda args: self.errors.append(list(args)))

    def get(self, address, args=(), timeout=TIMEOUT):
        key = (address, *list(args)[:KEY_LEN.get(address, 0)])
        return self.osc.request_many([(address, list(args))], KEY_LEN, timeout).get(key)

    def many(self, requests, timeout=TIMEOUT):
        return self.osc.request_many(requests, KEY_LEN, timeout)

    def send(self, address, args=()):
        self.osc.send(address, list(args))

    def _listen(self, address):
        if address in self.listening:
            return
        self.listening.add(address)

        def on_reply(args, address=address):
            with self.cond:
                self.inbox[address].append(list(args))
                self.cond.notify_all()

        self.osc.subscribe(address, on_reply)

    def pipeline(self, messages, timeout=TIMEOUT):
        """Send (address, args) in order; return {address: [reply args...]} for gets."""
        gets = [a for a, _ in messages if "/get/" in a]
        for address in set(gets):
            self._listen(address)
        want = defaultdict(int)
        for address in gets:
            want[address] += 1
        with self.cond:
            for address in want:
                self.inbox[address].clear()
        for address, args in messages:
            self.send(address, args)
        deadline = time.monotonic() + timeout
        with self.cond:
            while any(len(self.inbox[a]) < n for a, n in want.items()):
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(left)
            return {a: list(self.inbox[a]) for a in want}


def one(reply):
    return reply[0] if reply else None


# The main probe

def pick_tracks(wire, num, overrides):
    props = ["name", "is_foldable", "has_midi_input", "devices/name", "devices/class_name"]
    replies = wire.many([(f"/live/track/get/{p}", [t]) for t in range(num) for p in props])
    tracks = []
    for t in range(num):
        def get(p, t=t):
            return replies.get((f"/live/track/get/{p}", t))
        kind = "group" if one(get("is_foldable")) else (
            "midi" if one(get("has_midi_input")) else "audio")
        tracks.append({"index": t, "name": one(get("name")), "type": kind,
                       "devices": list(get("devices/name") or []),
                       "classes": list(get("devices/class_name") or [])})
    roles = {}
    for role in ("audio", "midi", "group"):
        if overrides.get(role) is not None:
            roles[role] = tracks[overrides[role]]
            continue
        # Prefer a track of that type that has a device, for the Device On test.
        candidates = [t for t in tracks if t["type"] == role]
        with_device = [t for t in candidates if t["devices"]]
        if with_device or candidates:
            roles[role] = (with_device or candidates)[0]
    return tracks, roles


def timed_write(wire, set_address, set_args, get_address, get_args):
    """Set, then read in the same tick. Returns (reply, seconds)."""
    started = time.monotonic()
    key = (get_address, *list(get_args)[:KEY_LEN.get(get_address, 0)])
    wire.send(set_address, set_args)
    reply = wire.osc.request_many([(get_address, list(get_args))], KEY_LEN, TIMEOUT).get(key)
    return reply, round(time.monotonic() - started, 4)


def probe_mixer(wire, role, track, out):
    t = track["index"]
    rows = []

    def record(kind, before, wanted, reply, secs, note=None):
        row = {"track": role, "kind": kind, "before": before, "set": wanted,
               "read_back": reply, "same_tick": reply == wanted if reply is not None else None,
               "seconds": secs}
        if note:
            row["note"] = note
        rows.append(row)
        out["timings"].append(secs)
        return row

    for kind in ("mute", "solo"):
        before = one(wire.get(f"/live/track/get/{kind}", [t]))
        if before is None:
            record(kind, None, None, None, None, "no reply to get")
            continue
        wanted = 0 if before else 1
        reply, secs = timed_write(wire, f"/live/track/set/{kind}", [t, wanted],
                                  f"/live/track/get/{kind}", [t])
        got = one(reply)
        row = record(kind, before, wanted, got, secs)
        if got is not None and bool(got) == bool(wanted):
            row["same_tick"] = True
            row["takes"] = "int"
        else:
            reply, secs = timed_write(wire, f"/live/track/set/{kind}", [t, bool(wanted)],
                                      f"/live/track/get/{kind}", [t])
            row["retry_with_bool"] = {"read_back": one(reply), "seconds": secs}
            if one(reply) is not None and bool(one(reply)) == bool(wanted):
                row["takes"] = "bool"
        wire.send(f"/live/track/set/{kind}", [t, int(bool(before))])
        row["restored"] = one(wire.get(f"/live/track/get/{kind}", [t]))

    for kind, value in (("volume", 0.5), ("panning", 0.25)):
        before = one(wire.get(f"/live/track/get/{kind}", [t]))
        if before is None:
            record(kind, None, None, None, None, "no reply to get")
            continue
        wanted = value if abs(before - value) > 1e-3 else value - 0.1
        reply, secs = timed_write(wire, f"/live/track/set/{kind}", [t, wanted],
                                  f"/live/track/get/{kind}", [t])
        got = one(reply)
        row = record(kind, before, wanted, got, secs)
        row["same_tick"] = got is not None and abs(got - wanted) < 1e-4
        wire.send(f"/live/track/set/{kind}", [t, float(before)])
        row["restored"] = one(wire.get(f"/live/track/get/{kind}", [t]))
        # Out of range: does Live clamp, refuse, or error?
        if kind == "volume":
            reply, secs = timed_write(wire, "/live/track/set/volume", [t, 1.2],
                                      "/live/track/get/volume", [t])
            row["set_out_of_range_1.2"] = one(reply)
            wire.send("/live/track/set/volume", [t, float(before)])

    before = wire.get("/live/track/get/send", [t, 0])
    if before is None:
        record("send", None, None, None, None, "no reply: no return track?")
    else:
        before = one(before)
        wanted = 0.5 if abs(before - 0.5) > 1e-3 else 0.4
        reply, secs = timed_write(wire, "/live/track/set/send", [t, 0, wanted],
                                  "/live/track/get/send", [t, 0])
        got = one(reply)
        row = record("send", before, wanted, got, secs)
        row["same_tick"] = got is not None and abs(got - wanted) < 1e-4
        wire.send("/live/track/set/send", [t, 0, float(before)])
        row["restored"] = one(wire.get("/live/track/get/send", [t, 0]))

    if track["devices"]:
        addr = "/live/device/get/parameter/value"
        before = one(wire.get(addr, [t, 0, 0]))
        if before is None:
            record("device_on", None, None, None, None, "no reply to get")
        else:
            wanted = 0.0 if before else 1.0
            reply, secs = timed_write(wire, "/live/device/set/parameter/value",
                                      [t, 0, 0, wanted], addr, [t, 0, 0])
            row = record("device_on", before, wanted, one(reply), secs)
            row["device"] = track["devices"][0]
            row["display"] = one(wire.get("/live/device/get/parameter/value_string", [t, 0, 0]))
            wire.send("/live/device/set/parameter/value", [t, 0, 0, float(before)])
            row["restored"] = one(wire.get(addr, [t, 0, 0]))
    else:
        record("device_on", None, None, None, None, "track has no devices")
    out["mixer"].extend(rows)


def device_params(wire, t, d):
    lists = ["name", "value", "min", "max", "is_quantized"]
    replies = wire.many([(f"/live/device/get/parameters/{p}", [t, d]) for p in lists])
    got = {p: replies.get((f"/live/device/get/parameters/{p}", t, d)) for p in lists}
    if any(v is None for v in got.values()):
        return None
    return [{"index": i, "name": got["name"][i], "original": got["value"][i],
             "min": got["min"][i], "max": got["max"][i],
             "is_quantized": bool(got["is_quantized"][i])} for i in range(len(got["name"]))]


def match(params, wanted):
    lower = {p["name"].lower(): p for p in params}
    if wanted.lower() in lower:
        return lower[wanted.lower()]
    for p in params:
        if wanted.lower() in p["name"].lower():
            return p
    return None


def sweep(wire, t, d, param, n):
    """Set the parameter across its range in one pipeline, reading value and text each time."""
    lo, hi = param["min"], param["max"]
    if param["is_quantized"] and hi - lo <= MAX_STEPS:
        raws = [float(v) for v in range(int(round(lo)), int(round(hi)) + 1)]
    else:
        raws = points(lo, hi, n)
    p = param["index"]
    messages = []
    for raw in raws:
        messages += [("/live/device/set/parameter/value", [t, d, p, raw]),
                     ("/live/device/get/parameter/value", [t, d, p]),
                     ("/live/device/get/parameter/value_string", [t, d, p])]
    messages.append(("/live/device/set/parameter/value", [t, d, p, float(param["original"])]))
    started = time.monotonic()
    replies = wire.pipeline(messages, timeout=TIMEOUT + len(raws) * 0.01)
    secs = round(time.monotonic() - started, 3)
    values = [r[3] for r in replies.get("/live/device/get/parameter/value", [])
              if r[:3] == [t, d, p]]
    texts = [r[3] for r in replies.get("/live/device/get/parameter/value_string", [])
             if r[:3] == [t, d, p]]
    curve = []
    for i, raw in enumerate(raws):
        curve.append([round(raw, 6),
                      texts[i] if i < len(texts) else None,
                      round(values[i], 6) if i < len(values) else None])
    return curve, secs


def probe_devices(wire, tracks, roles, out):
    preferred = [r["index"] for r in roles.values()]
    order = sorted(tracks, key=lambda tr: (tr["index"] not in preferred, tr["index"]))
    for class_name, keys in DEVICES.items():
        found = next(((tr, d) for tr in order for d, c in enumerate(tr["classes"])
                      if c == class_name), None)
        if not found:
            out["devices"].append({"class_name": class_name, "missing": True})
            log(f"  {class_name}: not found at the top level of any track")
            continue
        tr, d = found
        t = tr["index"]
        params = device_params(wire, t, d)
        if params is None:
            out["devices"].append({"class_name": class_name, "track": t, "index": d,
                                   "error": "no reply to parameter lists"})
            continue
        key_names = {}
        for wanted in keys:
            hit = match(params, wanted)
            if hit:
                key_names[hit["index"]] = wanted
        started = time.monotonic()
        for param in params:
            n = KEY_POINTS if param["index"] in key_names else OTHER_POINTS
            curve, secs = sweep(wire, t, d, param, n)
            param["key"] = param["index"] in key_names
            param["curve"] = curve
            param["pipeline_seconds"] = secs
            restored = one(wire.get("/live/device/get/parameter/value", [t, d, param["index"]]))
            param["restored"] = restored
            if restored is not None and abs(restored - param["original"]) > 1e-4:
                log(f"  ! {class_name} {param['name']} not restored: {restored} vs {param['original']}")
        out["devices"].append({
            "class_name": class_name, "name": tr["devices"][d], "track": t, "index": d,
            "key_parameters": [params[i]["name"] for i in key_names],
            "missing_key_parameters": [k for k in keys if k not in key_names.values()],
            "seconds": round(time.monotonic() - started, 2), "parameters": params,
        })
        log(f"  {class_name} on track {t} ({tr['name']}): {len(params)} parameters swept in "
            f"{time.monotonic() - started:.1f} s")
        for i in key_names:
            c = params[i]["curve"]
            log(f"    {params[i]['name']}: {c[0][1]} .. {c[len(c) // 2][1]} .. {c[-1][1]}")


def probe_undo(wire, roles, out):
    """How many undo steps a write leaves, alone and as 5 quick sets (one tick, and spread)."""
    t = roles["audio"]["index"] if "audio" in roles else 0
    dev = next((d for d in out["devices"] if not d.get("missing") and "parameters" in d), None)

    def reader(kind):
        if kind == "mute":
            return lambda: one(wire.get("/live/track/get/mute", [t]))
        if kind == "volume":
            return lambda: one(wire.get("/live/track/get/volume", [t]))
        tt, dd, pp = dev["track"], dev["index"], key_index(dev)
        return lambda: one(wire.get("/live/device/get/parameter/value", [tt, dd, pp]))

    def writer(kind):
        if kind == "mute":
            return lambda v: ("/live/track/set/mute", [t, int(v)])
        if kind == "volume":
            return lambda v: ("/live/track/set/volume", [t, float(v)])
        tt, dd, pp = dev["track"], dev["index"], key_index(dev)
        return lambda v: ("/live/device/set/parameter/value", [tt, dd, pp, float(v)])

    def key_index(device):
        keys = [p for p in device["parameters"] if p.get("key") and not p["is_quantized"]]
        return (keys or device["parameters"])[0]["index"]

    def undo_and_read(read, times):
        seen = []
        for _ in range(times):
            wire.send("/live/song/undo")
            time.sleep(0.25)
            seen.append(read())
        return seen

    kinds = ["mute", "volume"] + (["parameter"] if dev else [])
    for kind in kinds:
        read, write = reader(kind), writer(kind)
        v0 = read()
        if v0 is None:
            out["undo"].append({"kind": kind, "error": "no reply"})
            continue
        if kind == "mute":
            single, five = [1 - int(bool(v0))], [1 - int(bool(v0)), int(bool(v0))] * 2 + [1 - int(bool(v0))]
        elif kind == "volume":
            single, five = [0.6], [0.55, 0.6, 0.65, 0.7, 0.75]
        else:
            p = next(p for p in dev["parameters"] if p["index"] == key_index(dev))
            lo, hi = p["min"], p["max"]
            single = [lo + (hi - lo) * 0.3]
            five = [lo + (hi - lo) * f for f in (0.3, 0.35, 0.4, 0.45, 0.5)]
        row = {"kind": kind, "original": v0,
               "can_undo": one(wire.get("/live/song/get/can_undo"))}

        # One write, then one undo.
        wire.send(*write(single[0]))
        time.sleep(0.25)
        row["single"] = {"set": single[0], "read": read(), "after_undo": undo_and_read(read, 1)}

        # Five writes in one tick, then undo up to five times.
        for v in five:
            wire.send(*write(v))
        time.sleep(0.25)
        row["five_one_tick"] = {"set": five, "read": read(), "after_undos": undo_and_read(read, 5)}

        # Five writes a tick apart with a read-back each (like a correction loop).
        for v in five:
            wire.send(*write(v))
            time.sleep(0.12)
            read()
        row["five_spread"] = {"set": five, "read": read(), "after_undos": undo_and_read(read, 5)}

        wire.send(*write(v0))
        time.sleep(0.25)
        row["restored"] = read()
        out["undo"].append(row)
        log(f"  undo {kind}: single -> {row['single']['after_undo']}, "
            f"5 in one tick -> {row['five_one_tick']['after_undos']}, "
            f"5 spread -> {row['five_spread']['after_undos']} (original {v0})")


def main_probe(args):
    out = {"probe": "writes", "date": time.strftime("%Y-%m-%d"),
           "abletonosc": abletonosc_version(), "tracks": {}, "mixer": [], "timings": [],
           "devices": [], "undo": [], "errors": []}
    with OscClient(*PORTS) as osc:
        wire = Wire(osc)
        if wire.get("/live/test") is None:
            sys.exit("No answer from AbletonOSC on port 11000.")
        out["live_version"] = wire.get("/live/application/get/version")
        num = int(one(wire.get("/live/song/get/num_tracks")))
        tracks, roles = pick_tracks(wire, num, {"audio": args.audio, "midi": args.midi,
                                                "group": args.group})
        out["tracks"] = {role: {k: tr[k] for k in ("index", "name", "type", "devices")}
                         for role, tr in roles.items()}
        log(f"Live {out['live_version']}, {num} tracks; using "
            + ", ".join(f"{r} = [{tr['index']}] {tr['name']}" for r, tr in roles.items()))

        for role, tr in roles.items():
            probe_mixer(wire, role, tr, out)
        for row in out["mixer"]:
            log(f"  {row['track']:5} {row['kind']:9} {row.get('before')} -> {row.get('set')}: "
                f"read {row.get('read_back')} same tick {row.get('same_tick')} "
                f"in {row.get('seconds')} s" + (f" ({row['note']})" if row.get("note") else ""))

        if not args.skip_devices:
            log("Sweeping device parameters...")
            probe_devices(wire, tracks, roles, out)
        if not args.skip_undo:
            log("Undo steps...")
            probe_undo(wire, roles, out)
        out["errors"] = wire.errors

    times = [s for s in out["timings"] if s is not None]
    if times:
        out["write_seconds"] = {"median": statistics.median(times), "max": max(times),
                                "n": len(times)}
        log(f"write + read back: median {out['write_seconds']['median']} s, "
            f"max {out['write_seconds']['max']} s")
    del out["timings"]
    if out["errors"]:
        log(f"/live/error replies: {out['errors'][:5]}")
    print(json.dumps(out, indent=1, ensure_ascii=False))


# Listening and mixer helpers

def listen(args):
    with OscClient(*PORTS) as osc:
        wire = Wire(osc)
        if wire.get("/live/test") is None:
            sys.exit("No answer from AbletonOSC on port 11000.")
        num = int(one(wire.get("/live/song/get/num_tracks")))
        tracks, _ = pick_tracks(wire, num, {})
        found = next(((tr, d) for tr in tracks for d, c in enumerate(tr["classes"])
                      if c == "AutoFilter"), None)
        if not found:
            sys.exit("No Auto Filter at the top level of any track.")
        tr, d = found
        t = tr["index"]
        params = device_params(wire, t, d)
        p = match(params, "Frequency")
        lo, hi, v0 = p["min"], p["max"], p["original"]
        target = lo + (hi - lo) * 0.55
        # A secant-style approach: overshoot, then close in.
        loop = [target + (hi - lo) * f for f in (0.08, -0.04, 0.02, -0.01, 0.0)]
        log(f"Auto Filter on [{t}] {tr['name']}, Frequency now "
            f"{one(wire.get('/live/device/get/parameter/value_string', [t, d, p['index']]))}")
        for run in range(3):
            log(f"  run {run + 1}: 5 sets, one per tick")
            for v in loop:
                wire.send("/live/device/set/parameter/value", [t, d, p["index"], v])
                wire.get("/live/device/get/parameter/value_string", [t, d, p["index"]])
            time.sleep(2)
            wire.send("/live/device/set/parameter/value", [t, d, p["index"], v0])
            time.sleep(2)
        log("  done, Frequency set back")


def mixer_spread(args):
    with OscClient(*PORTS) as osc:
        wire = Wire(osc)
        if wire.get("/live/test") is None:
            sys.exit("No answer from AbletonOSC on port 11000.")
        num = int(one(wire.get("/live/song/get/num_tracks")))
        tracks, _ = pick_tracks(wire, num, {})
        regular = [tr for tr in tracks if tr["type"] != "group"]
        n = max(len(VOLUME_POINTS), len(PAN_POINTS))
        if len(regular) < n:
            log(f"Only {len(regular)} regular tracks; {n} would cover every point.")
        if RESTORE_FILE.exists():
            sys.exit(f"{RESTORE_FILE} exists: run --mixer-restore first.")
        saved = []
        rows = []
        for i, tr in enumerate(regular[:n]):
            t = tr["index"]
            old = {"index": t, "volume": one(wire.get("/live/track/get/volume", [t])),
                   "panning": one(wire.get("/live/track/get/panning", [t]))}
            saved.append(old)
            vol = VOLUME_POINTS[i] if i < len(VOLUME_POINTS) else None
            pan = PAN_POINTS[i] if i < len(PAN_POINTS) else None
            if vol is not None:
                wire.send("/live/track/set/volume", [t, vol])
            if pan is not None:
                wire.send("/live/track/set/panning", [t, pan])
            rows.append({"track": t, "name": tr["name"], "volume_raw": vol, "pan_raw": pan,
                         "volume_display": None, "pan_display": None})
        RESTORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        RESTORE_FILE.write_text(json.dumps(saved))
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        log("Read each track's volume and pan in Session view's mixer, fill in "
            "volume_display / pan_display, then run --mixer-restore.")


def mixer_restore(args):
    if not RESTORE_FILE.exists():
        sys.exit("Nothing to restore.")
    saved = json.loads(RESTORE_FILE.read_text())
    with OscClient(*PORTS) as osc:
        for old in saved:
            if old["volume"] is not None:
                osc.send("/live/track/set/volume", [old["index"], float(old["volume"])])
            if old["panning"] is not None:
                osc.send("/live/track/set/panning", [old["index"], float(old["panning"])])
        time.sleep(0.3)
    RESTORE_FILE.unlink()
    log(f"Restored volume and pan on {len(saved)} tracks.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--audio", type=int, help="audio track index to use")
    parser.add_argument("--midi", type=int, help="MIDI track index to use")
    parser.add_argument("--group", type=int, help="group track index to use")
    parser.add_argument("--skip-devices", action="store_true")
    parser.add_argument("--skip-undo", action="store_true")
    parser.add_argument("--listen", action="store_true", help="run the audible correction loop")
    parser.add_argument("--mixer-spread", action="store_true")
    parser.add_argument("--mixer-restore", action="store_true")
    parser.add_argument("--ports", help="SEND,RECEIVE instead of 11000,11001")
    args = parser.parse_args()
    if args.ports:
        PORTS[1:] = [int(p) for p in args.ports.split(",")]
    if args.listen:
        listen(args)
    elif args.mixer_spread:
        mixer_spread(args)
    elif args.mixer_restore:
        mixer_restore(args)
    else:
        main_probe(args)


if __name__ == "__main__":
    main()
