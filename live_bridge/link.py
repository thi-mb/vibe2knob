"""What the hub expects from a Live connection, real (LiveBridge) or fake (FakeLive).

Reads return plain dicts, so this package doesn't depend on vibe2knob:
- track_state(index): the track-state format in docs/track-state-format.md.
- session_summary(): every regular track, light enough to send with every chat turn:
  {"schema_version": 1, "live_version": "11.3" | None, "tempo": float | None,
   "tracks": [{"index", "name", "type", "color", "devices": [names], "volume", "panning",
   "mute"}]}
- arrangement(): where clips sit in Arrangement view, in beats from the song's start, or
  None when the link can't tell:
  {"schema_version": 1, "song_length": float | None, "signature": [4, 4] | None,
   "locators": [{"name", "time"}],
   "tracks": [{"index", "is_group", "is_grouped",
               "clips": [{"name", "start", "end"}] | None}]}
  Group tracks have clips None (their children carry the clips).
- session_view(): Session view's clip grid and what plays from it, or None when the link
  can't tell:
  {"schema_version": 1, "is_playing": bool | None, "scenes": [{"index", "name"}],
   "tracks": [{"index", "is_group", "slots": [{"name", "length"} | None],
               "playing_slot": int | None, "fired_slot": int | None}]}
  One slot per scene; length is in beats. Group slots are always None here.

Callbacks from subscribe_selection() and subscribe_status() run on a background thread.
Keep them short; they may call the read methods.
"""

import threading

CONNECTED = "connected"
RECONNECTING = "reconnecting"
OFFLINE = "offline"

SUMMARY_SCHEMA_VERSION = 1
ARRANGEMENT_SCHEMA_VERSION = 1
SESSION_VIEW_SCHEMA_VERSION = 1


class LiveError(RuntimeError):
    pass


class NotSupportedYet(LiveError):
    """A write to Live that's planned (Do mode) but not built yet."""


class LiveLink:
    def __init__(self):
        self._state_lock = threading.Lock()
        self._status = OFFLINE
        self._detail = "Not connected yet."
        self._selected = None
        self._selection_callbacks = []
        self._status_callbacks = []

    # Lifecycle

    def start(self):
        """Start watching Live (health and selection). Reads work without it."""

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # Watched state

    @property
    def status(self):
        return self._status

    @property
    def detail(self):
        """Why Live isn't connected, or None when it is."""
        return self._detail

    @property
    def selected(self):
        """The last selected track index seen by the watcher, or None."""
        return self._selected

    def subscribe_selection(self, callback):
        """Call `callback(index_or_None)` whenever the selected track changes."""
        return self._add(self._selection_callbacks, callback)

    def subscribe_status(self, callback):
        """Call `callback(status, detail)` whenever the connection status changes."""
        return self._add(self._status_callbacks, callback)

    def _add(self, callbacks, callback):
        with self._state_lock:
            callbacks.append(callback)

        def unsubscribe():
            with self._state_lock:
                if callback in callbacks:
                    callbacks.remove(callback)

        return unsubscribe

    def _set_selected(self, index):
        with self._state_lock:
            if index == self._selected:
                return
            self._selected = index
            callbacks = list(self._selection_callbacks)
        for callback in callbacks:
            callback(index)

    def _set_status(self, status, detail=None):
        with self._state_lock:
            if (status, detail) == (self._status, self._detail):
                return
            self._status, self._detail = status, detail
            callbacks = list(self._status_callbacks)
        for callback in callbacks:
            callback(status, detail)

    # Reads

    def ping(self):
        raise NotImplementedError

    def selected_track_index(self):
        """Ask Live for the selected track. None if it isn't a regular track."""
        raise NotImplementedError

    def track_state(self, track_index):
        raise NotImplementedError

    def session_summary(self):
        raise NotImplementedError

    def arrangement(self):
        return None

    def session_view(self):
        return None

    def selected_track_state(self):
        index = self.selected_track_index()
        if index is None:
            # AbletonOSC only indexes regular tracks, not returns or the master.
            raise LiveError("Couldn't read the selected track. Select an audio, MIDI or group track.")
        return self.track_state(index)

    # Writes (Do mode). Anything that changes Live must be shown, confirmed and undoable;
    # that flow lives in the hub. These only write and read back.
    #
    # Each write reads the value back in the same Live tick and returns what Live now holds:
    # {"value": raw, "display": str | None}. It raises LiveError when Live doesn't answer or
    # didn't take the value (out of range, or a device that went away), and ValueError for a
    # value that can't be right before it's sent. Devices are top-level only: AbletonOSC
    # can't reach devices inside rack chains (rack macros are the rack's own parameters).

    def set_mute(self, track_index, mute):
        raise NotSupportedYet("This Live connection can't mute tracks.")

    def set_solo(self, track_index, solo):
        raise NotSupportedYet("This Live connection can't solo tracks.")

    def set_volume(self, track_index, value):
        """Raw fader value, 0.0 to 1.0 (0.85 is 0 dB)."""
        raise NotSupportedYet("This Live connection can't change volume.")

    def set_panning(self, track_index, value):
        """Raw pan, -1.0 (50L) to 1.0 (50R)."""
        raise NotSupportedYet("This Live connection can't change panning.")

    def set_send(self, track_index, send_index, value):
        """Raw send level, 0.0 to 1.0."""
        raise NotSupportedYet("This Live connection can't change sends.")

    def set_device_on(self, track_index, device_index, on):
        raise NotSupportedYet("This Live connection can't turn devices on or off.")

    def set_parameter(self, track_index, device_index, parameter_index, value):
        """Raw parameter value, between the parameter's min and max (see parameter_info)."""
        raise NotSupportedYet("This Live connection can't change device parameters.")

    def parameter_info(self, track_index, device_index):
        """A top-level device's parameters as Live holds them now:
        {"track": int, "device": int, "name": str, "class_name": str,
         "parameters": [{"index", "name", "value", "min", "max", "is_quantized", "display"}]}
        """
        raise NotSupportedYet("This Live connection can't read device parameters.")

    def check_writes(self):
        """True if Live takes writes: sets a track's mute to what it already is, reads back."""
        return False


# Mixer values have no display string from Live; these match what Live's mixer shows.

def mute_display(on):
    return "on" if on else "off"


def pan_display(value):
    """Live's pan box: "C", "25L", "50R"."""
    amount = round(abs(value) * 50)
    if amount == 0:
        return "C"
    return f"{amount}{'L' if value < 0 else 'R'}"


def check_range(name, value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise ValueError(f"{name} must be a number, not {value!r}.")
    if not low <= value <= high:
        raise ValueError(f"{name} {value} is outside {low} to {high}.")
    return float(value)


def took(wanted, got):
    """Whether a read-back value matches what was set (Live stores 32-bit floats)."""
    if got is None:
        return False
    return abs(float(got) - float(wanted)) <= 1e-4 * max(1.0, abs(float(wanted)))
