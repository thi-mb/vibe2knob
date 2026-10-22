import json
import socket
import threading
import time

import pytest

from live_bridge import CONNECTED, OFFLINE, RECONNECTING, FakeLive, LiveBridge, LiveError
from live_bridge import NotSupportedYet
from live_bridge.osc import OscClient, decode_message, encode_message

from .conftest import FIXTURES


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeAbletonOSC:
    """Answers the OSC addresses the bridge uses, from track-state dicts, like AbletonOSC."""

    def __init__(self, states, reply_port):
        self.states = states
        self.reply_to = ("127.0.0.1", reply_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.selected = 0
        self.down = False
        self.listener_works = True
        self.listening = False
        self.received = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        self._thread.join()
        self.sock.close()

    def send(self, address, args):
        self.sock.sendto(encode_message(address, args), self.reply_to)

    def select(self, index):
        self.selected = index
        if self.listening and self.listener_works and index is not None:
            self.send("/live/view/get/selected_track", [index])

    def restart(self):
        """Live quit and came back: listeners are gone."""
        self.listening = False
        self.down = False

    def _serve(self):
        while not self._stop.is_set():
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                continue
            address, args = decode_message(data)
            self.received.append(address)
            if self.down:
                continue
            reply = self._answer(address, args)
            if reply is not None:
                self.send(address, reply)

    def _answer(self, address, args):
        if address == "/live/test":
            return ["ok"]
        if address == "/live/view/get/selected_track":
            return None if self.selected is None else [self.selected]
        if address == "/live/view/start_listen/selected_track":
            self.listening = True
            if self.listener_works and self.selected is not None:
                self.send("/live/view/get/selected_track", [self.selected])
            return None
        if address == "/live/view/stop_listen/selected_track":
            self.listening = False
            return None
        if address == "/live/song/get/num_tracks":
            return [len(self.states)]
        if address == "/live/song/get/tempo":
            return [120.0]
        if address == "/live/application/get/version":
            return [11, 3]
        if address.startswith("/live/track/get/"):
            t = args[0]
            return [t, *self._track_prop(self.states[t]["track"], address.removeprefix("/live/track/get/"))]
        if address.startswith("/live/device/get/"):
            t, d = args[0], args[1]
            device = self.states[t]["track"]["devices"][d]
            prop = address.removeprefix("/live/device/get/")
            if prop == "parameter/value_string":
                return [t, d, args[2], device["parameters"][args[2]]["display"] or ""]
            field = prop.removeprefix("parameters/")
            return [t, d, *[_osc_value(p[field]) for p in device["parameters"]]]
        return None

    def _track_prop(self, track, prop):
        mixer = track.get("mixer", {})
        devices = track.get("devices", [])
        values = {
            "name": track["name"],
            "color": track.get("color") or 0,
            "is_foldable": track["type"] == "group",
            "is_grouped": track.get("is_grouped", False),
            "has_midi_input": track["type"] == "midi",
            "has_audio_input": track["type"] == "audio",
            "can_be_armed": track["type"] != "group",
            "arm": bool(mixer.get("arm")),
            "mute": mixer.get("mute", False),
            "solo": mixer.get("solo", False),
            "volume": mixer.get("volume", 0.85),
            "panning": mixer.get("panning", 0.0),
            "num_devices": len(devices),
        }
        if prop.startswith("devices/"):
            field = prop.removeprefix("devices/")
            types = {"instrument": 1, "audio_effect": 2, "midi_effect": 4}
            out = []
            for d in devices:
                if field == "type":
                    out.append(types.get(d["type"], 0))
                elif field == "can_have_chains":
                    out.append(d["is_rack"])
                else:
                    out.append(d[field])
            return out
        return [values[prop]]


def _osc_value(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


@pytest.fixture
def live_set():
    states = FakeLive()._states
    receive_port = _free_port()
    server = FakeAbletonOSC(states, receive_port)
    bridges = []

    def connect(**kwargs):
        osc = OscClient("127.0.0.1", server.port, receive_port)
        kwargs = {"timeout": 0.3, "health_interval": 0.1, "poll_interval": 0.05, **kwargs}
        bridge = LiveBridge(osc=osc, **kwargs)
        bridges.append(bridge)
        return bridge

    yield server, connect
    for bridge in bridges:
        bridge.close()
    server.close()


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# OSC routing


def test_unclaimed_messages_reach_subscribers_while_a_request_waits():
    receive_port = _free_port()
    server = FakeAbletonOSC([], receive_port)
    with OscClient("127.0.0.1", server.port, receive_port) as osc:
        got = []
        osc.subscribe("/live/view/get/selected_track", got.append)
        # A push while a request is waiting would have been drained and lost before.
        server.listening = True
        threading.Timer(0.02, server.select, args=[4]).start()
        assert osc.request("/live/test", timeout=0.5) == ["ok"]
        assert _wait_for(lambda: got == [[4]])
    server.close()


def test_request_times_out_without_a_reply():
    receive_port = _free_port()
    server = FakeAbletonOSC([], receive_port)
    server.down = True
    with OscClient("127.0.0.1", server.port, receive_port) as osc:
        started = time.monotonic()
        assert osc.request("/live/test", timeout=0.2) is None
        assert time.monotonic() - started < 1
    server.close()


def test_concurrent_requests_each_get_their_reply(live_set):
    server, connect = live_set
    live = connect()
    results = {}

    def read(t):
        results[t] = live.track_state(t)["track"]["name"]

    threads = [threading.Thread(target=read, args=(t,)) for t in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert results == {t: server.states[t]["track"]["name"] for t in range(3)}


# Reads


def test_track_state_matches_the_fixture(live_set):
    server, connect = live_set
    live = connect()
    state = live.track_state(5)
    expected = server.states[5]["track"]
    assert state["track"]["name"] == expected["name"]
    assert [d["name"] for d in state["track"]["devices"]] == [d["name"] for d in expected["devices"]]
    first = state["track"]["devices"][0]["parameters"][0]
    assert first["display"] == expected["devices"][0]["parameters"][0]["display"]


def test_session_summary(live_set):
    server, connect = live_set
    summary = connect().session_summary()
    assert summary["schema_version"] == 1
    assert summary["live_version"] == "11.3"
    assert summary["tempo"] == 120.0
    assert len(summary["tracks"]) == len(server.states)
    for row, state in zip(summary["tracks"], server.states):
        track = state["track"]
        assert row["name"] == track["name"]
        assert row["type"] == track["type"]
        assert row["devices"] == [d["name"] for d in track["devices"]]
        assert set(row) == {"index", "name", "type", "color", "devices", "volume", "panning", "mute"}


def test_selected_track(live_set):
    server, connect = live_set
    live = connect()
    server.selected = 2
    assert live.selected_track_index() == 2
    assert live.selected_track_state()["track"]["index"] == 2
    server.selected = None  # a return track or the master
    assert live.selected_track_index() is None
    with pytest.raises(LiveError, match="Select an audio"):
        live.selected_track_state()


def test_writes_are_not_supported_yet(live_set):
    _, connect = live_set
    live = connect()
    for call in (lambda: live.set_mute(0, True), lambda: live.set_solo(0, True),
                 lambda: live.set_parameter(0, 0, 0, 0.5)):
        with pytest.raises(NotSupportedYet):
            call()


# Watching


def test_selection_follows_the_listener(live_set):
    server, connect = live_set
    live = connect(poll_selection=False)
    seen = []
    live.subscribe_selection(seen.append)
    live.start()
    assert _wait_for(lambda: live.status == CONNECTED and seen == [0])
    server.select(3)
    assert _wait_for(lambda: seen == [0, 3])
    assert live.selected == 3


def test_selection_falls_back_to_polling(live_set):
    server, connect = live_set
    server.listener_works = False
    live = connect()
    seen = []
    live.subscribe_selection(seen.append)
    live.start()
    assert _wait_for(lambda: seen == [0])
    server.select(6)
    assert _wait_for(lambda: seen == [0, 6])
    server.select(None)
    assert _wait_for(lambda: seen == [0, 6, None])


def test_health_goes_reconnecting_then_offline_and_resubscribes(live_set):
    server, connect = live_set
    live = connect(poll_selection=False)
    statuses = []
    live.subscribe_status(lambda status, detail: statuses.append(status))
    live.start()
    assert _wait_for(lambda: statuses == [CONNECTED])
    assert server.listening

    server.down = True
    assert _wait_for(lambda: statuses == [CONNECTED, RECONNECTING, OFFLINE])
    assert live.selected is None
    assert "AbletonOSC" in live.detail

    server.restart()
    server.selected = 1
    assert _wait_for(lambda: statuses[-1] == CONNECTED)
    assert server.listening
    assert _wait_for(lambda: live.selected == 1)


# FakeLive


def test_fake_live_from_fixtures():
    live = FakeLive()
    files = sorted(FIXTURES.glob("*.json"))
    summary = live.session_summary()
    assert [t["index"] for t in summary["tracks"]] == list(range(len(files)))
    assert live.track_state(1)["track"]["index"] == 1
    assert live.selected_track_index() == 0

    seen = []
    live.subscribe_selection(seen.append)
    live.select(4)
    live.select(4)
    assert seen == [4]
    assert live.selected_track_state()["track"]["index"] == 4

    statuses = []
    live.subscribe_status(lambda s, d: statuses.append(s))
    live.set_connected(False)
    assert statuses == [OFFLINE] and seen == [4, None]
    with pytest.raises(LiveError):
        live.session_summary()
    with pytest.raises(NotSupportedYet):
        live.set_mute(0, True)


def test_fake_live_from_dicts():
    live = FakeLive([{"track": {"name": "Bass", "type": "midi"}}], selected=None)
    assert live.selected_track_index() is None
    assert live.session_summary()["tracks"][0]["devices"] == []


# Arrangement


class ProbeOSC(FakeAbletonOSC):
    """Replays a probe of a real Live 11.3 set (fixtures/arrangement), group tracks unanswered."""

    def __init__(self, probe, reply_port, *, arrangement_works=True):
        self.probe = probe
        self.arrangement_works = arrangement_works
        super().__init__([], reply_port)

    def _answer(self, address, args):
        song = address.removeprefix("/live/song/get/")
        if song in self.probe["song"]:
            return self.probe["song"][song]
        if address == "/live/song/get/num_tracks":
            return [len(self.probe["tracks"])]
        if address.startswith("/live/track/get/"):
            prop = address.removeprefix("/live/track/get/")
            if prop.startswith("arrangement_clips/") and not self.arrangement_works:
                return None
            value = self.probe["tracks"][args[0]].get(prop)
            return None if value is None else [args[0], *value]
        return super()._answer(address, args)


@pytest.fixture
def probe_set():
    probe = json.loads((FIXTURES / "arrangement" / "probe_live11.json").read_text())
    servers, bridges = [], []

    def connect(**kwargs):
        receive_port = _free_port()
        server = ProbeOSC(probe, receive_port, **kwargs)
        servers.append(server)
        bridge = LiveBridge(osc=OscClient("127.0.0.1", server.port, receive_port), timeout=0.3)
        bridges.append(bridge)
        return server, bridge

    yield connect
    for bridge in bridges:
        bridge.close()
    for server in servers:
        server.close()


def test_arrangement_from_a_real_set(probe_set):
    server, live = probe_set()
    started = time.monotonic()
    arrangement = live.arrangement()
    # Group tracks aren't asked, so nothing waits out the timeout.
    assert time.monotonic() - started < 0.3
    assert arrangement["signature"] == [4, 4]
    assert arrangement["song_length"] == 504.0
    assert arrangement["locators"] == []
    tracks = arrangement["tracks"]
    assert len(tracks) == 39
    vocals, chop, trumpet = tracks[0], tracks[1], tracks[19]
    assert vocals["is_group"] and vocals["clips"] is None
    assert chop["is_grouped"] and len(chop["clips"]) == 23
    # Live reports these four clips as 24 beats each; each one ends where the next starts.
    assert [(c["start"], c["end"]) for c in trumpet["clips"][-4:]] == [
        (432.0, 450.0), (450.0, 452.0), (452.0, 454.0), (454.0, 478.0)]
    assert tracks[35]["clips"] == []


def test_arrangement_with_an_old_abletonosc(probe_set):
    server, live = probe_set(arrangement_works=False)
    assert live.arrangement() is None
    asked = len(server.received)
    assert live.arrangement() is None
    assert len(server.received) == asked  # remembered, so later turns don't wait again


def test_clip_spans():
    from live_bridge.client import _clip_spans

    spans = _clip_spans(["b", "a", "c"], [8.0, 0.0, 9.0], [4.0, 16.0, 0.0])
    assert spans == [{"name": "a", "start": 0.0, "end": 8.0}, {"name": "b", "start": 8.0, "end": 9.0}]


def test_fake_live_arrangement():
    live = FakeLive()
    arrangement = live.arrangement()
    assert [c["name"] for c in arrangement["locators"]][:3] == ["Intro", "Verse", "Chorus"]
    assert len(arrangement["tracks"]) == len(live._states)
    assert FakeLive([FIXTURES / "synth_pad.json"]).arrangement() is None
