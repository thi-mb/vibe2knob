"""Reads Live's state through AbletonOSC.

AbletonOSC rides on Live's unofficial remote-script API, so every OSC address the
project depends on lives in this file. If Live or AbletonOSC changes, this is the
only place that should need fixing.
"""

import threading
import time

from .link import (ARRANGEMENT_SCHEMA_VERSION, CONNECTED, OFFLINE, RECONNECTING,
                   SUMMARY_SCHEMA_VERSION, LiveError, LiveLink, NotSupportedYet)
from .osc import OscClient

__all__ = ["LiveBridge", "LiveError", "NotSupportedYet"]

SCHEMA_VERSION = 1

_SELECTED = "/live/view/get/selected_track"
_LISTEN_SELECTED = "/live/view/start_listen/selected_track"
_STOP_LISTEN_SELECTED = "/live/view/stop_listen/selected_track"

# How many leading reply args identify the object (track id, device id, ...).
_KEY_LEN = {
    "/live/test": 0,
    _SELECTED: 0,
    "/live/song/get/num_tracks": 0,
    "/live/song/get/tempo": 0,
    "/live/application/get/version": 0,
    "/live/device/get/parameter/value_string": 3,
}

_TRACK_PROPS = [
    "name", "color", "is_foldable", "is_grouped", "has_midi_input",
    "has_audio_input", "can_be_armed", "mute", "solo", "volume", "panning",
    "num_devices",
]
_TRACK_LISTS = ["devices/name", "devices/class_name", "devices/type", "devices/can_have_chains"]
_DEVICE_LISTS = [
    "parameters/name", "parameters/value", "parameters/min", "parameters/max",
    "parameters/is_quantized",
]

# Live's DeviceType enum, as AbletonOSC reports it.
_DEVICE_TYPES = {0: "undefined", 1: "instrument", 2: "audio_effect", 4: "midi_effect"}

# What session_summary() reads for every track, in one batch.
_SUMMARY_PROPS = ["name", "color", "is_foldable", "has_midi_input", "volume", "panning", "mute",
                  "devices/name"]

# What arrangement() reads. AbletonOSC has no clip end_time, so ends come from length.
_SONG_PROPS = ["song_length", "signature_numerator", "signature_denominator", "cue_points"]
_ARRANGEMENT_LISTS = ["arrangement_clips/name", "arrangement_clips/start_time",
                      "arrangement_clips/length"]

for _p in _SONG_PROPS:
    _KEY_LEN[f"/live/song/get/{_p}"] = 0
for _p in _TRACK_PROPS + _TRACK_LISTS + _ARRANGEMENT_LISTS + ["arm"]:
    _KEY_LEN[f"/live/track/get/{_p}"] = 1
for _p in _DEVICE_LISTS:
    _KEY_LEN[f"/live/device/get/{_p}"] = 2


_NO_ANSWER = (
    "No answer from AbletonOSC on port 11000. Is Live open with AbletonOSC selected as a "
    "Control Surface?"
)

# Failed health pings in a row before "reconnecting" becomes "offline".
_OFFLINE_AFTER = 3


def _clip_spans(names, starts, lengths):
    """Clips as {name, start, end}, in order.

    `length` is the clip's own length, which can run past where the next clip on the track
    starts (measured on Live 11.3), and clips on one track never overlap, so each clip
    ends at the next one's start at the latest.
    """
    clips = sorted(zip(starts, lengths, names), key=lambda c: c[0])
    out = []
    for i, (start, length, name) in enumerate(clips):
        end = start + length
        if i + 1 < len(clips):
            end = min(end, clips[i + 1][0])
        if end > start:
            out.append({"name": name, "start": start, "end": end})
    return out


def _track_type(is_foldable, has_midi_input):
    if is_foldable:
        return "group"
    return "midi" if has_midi_input else "audio"


class LiveBridge(LiveLink):
    """The one owner of the AbletonOSC connection. Reads are serialised by a lock.

    Reads work as soon as it's created. start() adds a watcher thread that pings Live
    every few seconds, tracks the selected track (Live's listener plus a poll, since the
    listener isn't proven on every Live + AbletonOSC version) and re-subscribes after Live
    comes back.
    """

    def __init__(self, host="127.0.0.1", timeout=2.0, *, osc=None,
                 health_interval=3.0, poll_interval=1.0, poll_selection=True):
        super().__init__()
        self._osc = osc or OscClient(host)
        self._timeout = timeout
        # Short waits for the watcher, so a missing reply doesn't hold the lock for long.
        self._quick = min(timeout, 1.0)
        self._io_lock = threading.RLock()
        self._health_interval = health_interval
        self._poll_interval = poll_interval
        self._poll_selection = poll_selection
        self._stop = threading.Event()
        self._watcher = None
        self._unsubscribe_push = None
        # False once AbletonOSC has shown it can't read arrangement clips (an old version),
        # so later turns don't each wait out the timeout. Reset when Live comes back.
        self._arrangement_works = True

    def close(self):
        self._stop.set()
        if self._watcher:
            self._watcher.join(timeout=self._health_interval + self._timeout + 1)
        if self._unsubscribe_push:
            self._unsubscribe_push()
            try:
                self._osc.send(_STOP_LISTEN_SELECTED)
            except OSError:
                pass
        self._osc.close()

    def _batch(self, requests, timeout=None):
        with self._io_lock:
            return self._osc.request_many(requests, _KEY_LEN, timeout or self._timeout)

    def _request(self, address, args=(), timeout=None):
        address_args = list(args)
        key = (address, *address_args[:_KEY_LEN.get(address, 0)])
        return self._batch([(address, address_args)], timeout).get(key)

    # Watching

    def start(self):
        if self._watcher:
            return
        self._unsubscribe_push = self._osc.subscribe(_SELECTED, self._on_selected_push)
        self._watcher = threading.Thread(target=self._watch, name="live-watch", daemon=True)
        self._watcher.start()

    def _on_selected_push(self, args):
        self._set_selected(int(args[0]) if args else None)

    def _watch(self):
        failures = 0
        next_ping = 0.0
        while not self._stop.is_set():
            if time.monotonic() >= next_ping:
                next_ping = time.monotonic() + self._health_interval
                if self._request("/live/test", timeout=self._quick) is not None:
                    failures = 0
                    if self.status != CONNECTED:
                        # A fresh start or Live came back: listeners don't survive a
                        # restart of Live, so subscribe again.
                        self._osc.send(_LISTEN_SELECTED)
                        self._arrangement_works = True
                        self._set_status(CONNECTED)
                else:
                    failures += 1
                    self._set_selected(None)
                    if self.status == CONNECTED or (
                        self.status == RECONNECTING and failures < _OFFLINE_AFTER
                    ):
                        self._set_status(RECONNECTING, "Lost Live. Trying again.")
                    else:
                        self._set_status(OFFLINE, _NO_ANSWER)
            if self.status == CONNECTED and self._poll_selection:
                self._set_selected(self._read_selected(self._quick))
            self._stop.wait(self._poll_interval)

    # Reads

    def ping(self):
        if self._request("/live/test") is None:
            raise LiveError(_NO_ANSWER)

    def _read_selected(self, timeout=None):
        reply = self._request(_SELECTED, timeout=timeout)
        # AbletonOSC only indexes regular tracks; return tracks and the master make the
        # request fail inside Live, so no reply comes back.
        return int(reply[0]) if reply else None

    def selected_track_index(self):
        return self._read_selected()

    def session_summary(self):
        with self._io_lock:
            head = self._batch([
                ("/live/song/get/num_tracks", []),
                ("/live/song/get/tempo", []),
                ("/live/application/get/version", []),
            ])
            num = head.get(("/live/song/get/num_tracks",))
            if not num:
                raise LiveError(_NO_ANSWER)
            count = int(num[0])
            replies = self._batch(
                [(f"/live/track/get/{p}", [t]) for t in range(count) for p in _SUMMARY_PROPS]
            )

        tempo = head.get(("/live/song/get/tempo",))
        version = head.get(("/live/application/get/version",))
        tracks = []
        for t in range(count):
            def get(name, t=t):
                return replies.get((f"/live/track/get/{name}", t))

            def one(name, t=t):
                value = get(name, t)
                return value[0] if value else None

            if get("name") is None:
                raise LiveError(f"No reply for track {t}. Did the set change while reading?")
            tracks.append({
                "index": t,
                "name": one("name"),
                "type": _track_type(one("is_foldable"), one("has_midi_input")),
                "color": one("color"),
                "devices": list(get("devices/name") or []),
                "volume": one("volume"),
                "panning": one("panning"),
                "mute": bool(one("mute")),
            })
        return {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "live_version": ".".join(str(v) for v in version[:2]) if version else None,
            "tempo": tempo[0] if tempo else None,
            "tracks": tracks,
        }

    def arrangement(self):
        if not self._arrangement_works:
            return None
        with self._io_lock:
            head = self._batch([(f"/live/song/get/{p}", []) for p in _SONG_PROPS + ["num_tracks"]])
            num = head.get(("/live/song/get/num_tracks",))
            if not num:
                raise LiveError(_NO_ANSWER)
            count = int(num[0])
            kinds = self._batch([(f"/live/track/get/{p}", [t]) for t in range(count)
                                 for p in ("is_foldable", "is_grouped")])

            def kind(name, t):
                value = kinds.get((f"/live/track/get/{name}", t))
                if value is None:
                    raise LiveError(f"No reply for track {t}. Did the set change while reading?")
                return bool(value[0])

            groups = {t for t in range(count) if kind("is_foldable", t)}
            # AbletonOSC doesn't answer arrangement_clips on group tracks, and every
            # unanswered read holds the batch for the whole timeout, so don't ask.
            replies = self._batch([(f"/live/track/get/{p}", [t]) for t in range(count)
                                   if t not in groups for p in _ARRANGEMENT_LISTS])

        tracks = []
        answered = False
        for t in range(count):
            clips = None
            if t not in groups:
                lists = [replies.get((f"/live/track/get/{p}", t)) for p in _ARRANGEMENT_LISTS]
                if all(v is not None for v in lists):
                    answered = True
                    clips = _clip_spans(*lists)
            tracks.append({"index": t, "is_group": t in groups,
                           "is_grouped": kind("is_grouped", t), "clips": clips})
        if not answered and len(groups) < count:
            self._arrangement_works = False
            return None

        def one(name):
            value = head.get((f"/live/song/get/{name}",))
            return value[0] if value else None

        cues = list(head.get(("/live/song/get/cue_points",)) or [])
        numerator, denominator = one("signature_numerator"), one("signature_denominator")
        return {
            "schema_version": ARRANGEMENT_SCHEMA_VERSION,
            "song_length": one("song_length"),
            "signature": [int(numerator), int(denominator)] if numerator and denominator else None,
            "locators": sorted(
                ({"name": cues[i], "time": cues[i + 1]} for i in range(0, len(cues) - 1, 2)),
                key=lambda c: c["time"],
            ),
            "tracks": tracks,
        }

    def track_state(self, track_index):
        with self._io_lock:
            return self._track_state(track_index)

    def _track_state(self, track_index):
        t = track_index
        replies = self._batch(
            [(f"/live/track/get/{p}", [t]) for p in _TRACK_PROPS + _TRACK_LISTS]
        )

        def prop(name):
            value = replies.get((f"/live/track/get/{name}", t))
            if value is None:
                raise LiveError(f"No reply for track {t} property '{name}'")
            return value

        def one(name):
            return prop(name)[0]

        track_type = _track_type(one("is_foldable"), one("has_midi_input"))

        arm = None
        if one("can_be_armed"):
            reply = self._batch([("/live/track/get/arm", [t])]).get(("/live/track/get/arm", t))
            arm = bool(reply[0]) if reply else None

        names = prop("devices/name")
        class_names = prop("devices/class_name")
        types = prop("devices/type")
        can_have_chains = prop("devices/can_have_chains")
        devices = [self._device_state(t, d) for d in range(len(names))]
        for d, device in enumerate(devices):
            device.update({
                "name": names[d],
                "class_name": class_names[d],
                "type": _DEVICE_TYPES.get(types[d], str(types[d])),
                "is_rack": bool(can_have_chains[d]),
            })
            # Put identity fields first so the JSON reads top-down.
            devices[d] = {k: device[k] for k in ("index", "name", "class_name", "type", "is_rack", "parameters")}

        return {
            "schema_version": SCHEMA_VERSION,
            "track": {
                "index": t,
                "name": one("name"),
                "type": track_type,
                "color": one("color"),
                "is_grouped": bool(one("is_grouped")),
                "mixer": {
                    "volume": one("volume"),
                    "panning": one("panning"),
                    "mute": bool(one("mute")),
                    "solo": bool(one("solo")),
                    "arm": arm,
                },
                "devices": devices,
            },
        }

    def _device_state(self, t, d):
        replies = self._batch([(f"/live/device/get/{p}", [t, d]) for p in _DEVICE_LISTS])

        def lst(name):
            value = replies.get((f"/live/device/get/{name}", t, d))
            if value is None:
                raise LiveError(f"No reply for device {t}/{d} '{name}'")
            return value

        names = lst("parameters/name")
        values = lst("parameters/value")
        mins = lst("parameters/min")
        maxs = lst("parameters/max")
        quantized = lst("parameters/is_quantized")

        # Display strings ("2.50 kHz", "-6.0 dB") need one request per parameter;
        # they're sent together so Live answers them in a single tick.
        addr = "/live/device/get/parameter/value_string"
        strings = self._batch([(addr, [t, d, p]) for p in range(len(names))])

        parameters = []
        for p, name in enumerate(names):
            display = strings.get((addr, t, d, p))
            parameters.append({
                "index": p,
                "name": name,
                "value": values[p],
                "min": mins[p],
                "max": maxs[p],
                "is_quantized": bool(quantized[p]),
                "display": display[0] if display else None,
            })
        return {"index": d, "parameters": parameters}
