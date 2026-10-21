"""Reads Live's state through AbletonOSC.

AbletonOSC rides on Live's unofficial remote-script API, so every OSC address the
project depends on lives in this file. If Live or AbletonOSC changes, this is the
only place that should need fixing.
"""

from .osc import OscClient

SCHEMA_VERSION = 1

# How many leading reply args identify the object (track id, device id, ...).
_KEY_LEN = {
    "/live/test": 0,
    "/live/view/get/selected_track": 0,
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

for _p in _TRACK_PROPS + _TRACK_LISTS + ["arm"]:
    _KEY_LEN[f"/live/track/get/{_p}"] = 1
for _p in _DEVICE_LISTS:
    _KEY_LEN[f"/live/device/get/{_p}"] = 2


class LiveError(RuntimeError):
    pass


class LiveBridge:
    def __init__(self, host="127.0.0.1", timeout=2.0):
        self._osc = OscClient(host)
        self._timeout = timeout

    def close(self):
        self._osc.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _batch(self, requests):
        return self._osc.request_many(requests, _KEY_LEN, self._timeout)

    def ping(self):
        if self._osc.request("/live/test", timeout=self._timeout) is None:
            raise LiveError(
                "No answer from AbletonOSC on port 11000. Is Live open with AbletonOSC "
                "selected as a Control Surface?"
            )

    def selected_track_index(self):
        reply = self._osc.request("/live/view/get/selected_track", timeout=self._timeout)
        if not reply:
            # AbletonOSC only indexes regular tracks; return tracks and the master
            # make the request fail inside Live, so no reply comes back.
            raise LiveError("Couldn't read the selected track. Select an audio, MIDI or group track.")
        return int(reply[0])

    def track_state(self, track_index):
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

        if one("is_foldable"):
            track_type = "group"
        elif one("has_midi_input"):
            track_type = "midi"
        else:
            track_type = "audio"

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

    def selected_track_state(self):
        return self.track_state(self.selected_track_index())
