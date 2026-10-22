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

Callbacks from subscribe_selection() and subscribe_status() run on a background thread.
Keep them short; they may call the read methods.
"""

import threading

CONNECTED = "connected"
RECONNECTING = "reconnecting"
OFFLINE = "offline"

SUMMARY_SCHEMA_VERSION = 1
ARRANGEMENT_SCHEMA_VERSION = 1


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

    def selected_track_state(self):
        index = self.selected_track_index()
        if index is None:
            # AbletonOSC only indexes regular tracks, not returns or the master.
            raise LiveError("Couldn't read the selected track. Select an audio, MIDI or group track.")
        return self.track_state(index)

    # Writes (Do mode). Anything that changes Live must be shown, confirmed and undoable,
    # so these stay off until that flow exists.

    def set_mute(self, track_index, mute):
        raise NotSupportedYet("Muting tracks isn't supported yet.")

    def set_solo(self, track_index, solo):
        raise NotSupportedYet("Soloing tracks isn't supported yet.")

    def set_parameter(self, track_index, device_index, parameter_index, value):
        raise NotSupportedYet("Changing device parameters isn't supported yet.")
