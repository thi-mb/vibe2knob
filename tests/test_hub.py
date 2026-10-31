import copy
import json
import threading

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from live_bridge import FakeLive  # noqa: E402
from vibe2knob.chat import Chat  # noqa: E402
from vibe2knob.hub import Hub  # noqa: E402
from vibe2knob.providers import ProviderError  # noqa: E402
from vibe2knob.providers.fake import FakeProvider, Reply  # noqa: E402
from vibe2knob.server import Access, create_app  # noqa: E402
from vibe2knob.store import MemoryKeys, Store, StoredSettings  # noqa: E402
from vibe2knob.usage import UsageRecord  # noqa: E402

from .conftest import FIXTURES, WIDER_PAD  # noqa: E402

TOKEN = "secret-token"
HOST = "testserver"


def wider_reply():
    return Reply.with_steps(
        "Open up the Utility a little.",
        copy.deepcopy(WIDER_PAD["steps"][:1]),
        WIDER_PAD["listen_for"],
        WIDER_PAD["caution"],
    )


class Setup:
    def __init__(self, tmp_path, provider=None, live=None, key="sk-test"):
        self.store = Store(tmp_path, keys=MemoryKeys(key))
        self.live = live if live is not None else FakeLive()
        self.provider = provider or FakeProvider([wider_reply()], title="Wider pad")
        self.examples = [json.loads((FIXTURES / "lead_vocal.json").read_text())]

        def make_provider(settings, api_key):
            if not api_key:
                raise ProviderError("No Anthropic API key yet. Add one in Settings.")
            return self.provider

        self.hub = Hub(self.store, self.live, make_provider=make_provider,
                       examples=self.examples, make_example=lambda s: FakeLive([s]))
        self.app = create_app(self.hub, Access(TOKEN, {HOST}), None)


@pytest.fixture
def setup(tmp_path):
    return Setup(tmp_path)


def connect(client, token=TOKEN, **kw):
    return client.websocket_connect(f"/ws?token={token}", **kw)


def hello(ws):
    ws.send_json({"v": 1, "type": "hello", "client": "browser", "request_id": "h"})
    return until(ws, "welcome")


def until(ws, kind):
    """Read messages until one of `kind` arrives; return it (earlier ones are dropped)."""
    for _ in range(500):
        m = ws.receive_json()
        if m["type"] == kind:
            return m
    raise AssertionError(f"no {kind}")


def collect_turn(ws):
    """Every message up to and including chat.done."""
    out = []
    while True:
        m = ws.receive_json()
        out.append(m)
        if m["type"] == "chat.done":
            return out


def create_chat(ws, **context):
    ws.send_json({"v": 1, "type": "chat.create", "request_id": "c", "context": context})
    m = until(ws, "chat.changed")
    assert m["request_id"] == "c"
    return m["chat"]["id"]


# Access


def test_welcome(setup):
    with TestClient(setup.app) as client, connect(client) as ws:
        m = hello(ws)
    assert m["v"] == 1
    assert m["chats"] == []
    assert m["live"]["connected"] is True
    selected = m["live"]["selected_track"]
    assert selected["index"] == 0 and selected["name"] == "acoustic-pick"
    assert "Chorus" in selected["devices"]
    # No model saved yet: Settings shows the one the chats will really use.
    assert m["settings"] == {"provider": "claude", "model": "claude-haiku-5-5", "api_key_set": True,
                             "monthly_budget_usd": None, "default_mode": "teach",
                             "auto_actions": []}
    assert [e["name"] for e in m["examples"]] == ["Lead Vox"]
    assert m["examples"][0]["devices"] == ["EQ Eight", "Compressor", "Reverb"]
    assert m["live"]["can_write"] is False
    assert m["voice"] == {"available": False, "reason": None, "model": None}


@pytest.mark.parametrize("token,headers", [
    ("wrong", {}),
    ("", {}),
    (TOKEN, {"origin": "https://evil.example"}),
    (TOKEN, {"host": "evil.example"}),
])
def test_rejected_sockets_close_with_4401(setup, token, headers):
    with TestClient(setup.app) as client, connect(client, token, headers=headers) as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4401


def test_allowed_origin(setup):
    with TestClient(setup.app) as client:
        with connect(client, headers={"origin": f"http://{HOST}"}) as ws:
            assert hello(ws)["type"] == "welcome"


def test_http_host_check(setup):
    with TestClient(setup.app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/", headers={"host": "evil.example"}).status_code == 403


def test_access_local():
    access = Access.local(8765, "t")
    assert access.allows("127.0.0.1:8765", "http://localhost:8765", "t")
    assert not access.allows("127.0.0.1:5173", None, "t")
    assert not access.allows("127.0.0.1:8765", None, "x")
    assert Access.local(8765, "t", dev=True).allows("localhost:5173", "http://localhost:5173", "t")
    assert len(Access.local(8765).token) >= 24


# Protocol basics


def test_bad_messages(setup):
    with TestClient(setup.app) as client, connect(client) as ws:
        ws.send_text("not json")
        assert ws.receive_json()["code"] == "bad_request"
        ws.send_json({"v": 2, "type": "hello", "client": "panel", "request_id": "x"})
        m = ws.receive_json()
        assert (m["code"], m["request_id"]) == ("unsupported_version", "x")
        ws.send_json({"v": 1, "type": "nope", "request_id": "y"})
        m = ws.receive_json()
        assert (m["code"], m["request_id"]) == ("bad_request", "y")
        ws.send_json({"v": 1, "type": "chat.send", "chat_id": "missing", "text": "hi"})
        assert ws.receive_json()["code"] == "not_found"


# Chats


def test_chat_turn_streams_and_saves(setup):
    with TestClient(setup.app) as client, connect(client) as ws:
        hello(ws)
        chat_id = create_chat(ws, follow_selection=True)
        ws.send_json({"v": 1, "type": "chat.send", "chat_id": chat_id, "text": "make it wider"})
        turn = collect_turn(ws)
        kinds = [m["type"] for m in turn]
        assert kinds[-1] == "chat.done" and "chat.steps" in kinds
        # The client hears what the turn is doing before the first word arrives.
        phases = [m["phase"] for m in turn if m["type"] == "chat.status"]
        assert phases == ["reading", "thinking", "steps"]
        assert kinds.index("chat.status") < kinds.index("chat.delta")
        deltas = [m for m in turn if m["type"] == "chat.delta"]
        assert "".join(d["text"] for d in deltas) == "Open up the Utility a little."
        turn_id = turn[-1]["turn_id"]
        assert {m["turn_id"] for m in turn if "turn_id" in m} == {turn_id}
        steps = next(m for m in turn if m["type"] == "chat.steps")
        assert steps["steps"][0]["device"] == "Utility"
        titled = until(ws, "chat.changed")
        while titled["chat"]["title"] is None:
            titled = until(ws, "chat.changed")
        assert titled["chat"]["title"] == "Wider pad"

        ws.send_json({"v": 1, "type": "chat.open", "chat_id": chat_id, "request_id": "o"})
        history = until(ws, "chat.history")
    assert history["request_id"] == "o"
    user, answer = history["turns"]
    assert (user["role"], user["text"]) == ("user", "make it wider")
    assert answer["id"] == turn_id
    assert answer["steps"][0]["device"] == "Utility"
    # The model saw the selected track from Live.
    assert "acoustic-pick" in setup.provider.calls[0]["messages"][-1]["content"]
    saved = Chat.load(setup.store.chats_dir / f"{chat_id}.json")
    assert saved.title == "Wider pad" and len(saved.turns) == 2


def test_a_read_turn_says_what_it_read(tmp_path):
    provider = FakeProvider([Reply.reads("Sub Bass", "Beat", reason="the low end"),
                             Reply("The kick and the bass share 60 Hz.")])
    setup = Setup(tmp_path, provider=provider)
    with TestClient(setup.app) as client, connect(client) as ws:
        hello(ws)
        chat_id = create_chat(ws, follow_selection=True)
        ws.send_json({"v": 1, "type": "chat.send", "chat_id": chat_id, "text": "muddy low end"})
        turn = collect_turn(ws)
        statuses = [(m["phase"], m["detail"]) for m in turn if m["type"] == "chat.status"]
        assert ("reading", "Reading Sub Bass, Beat") in statuses
        looked = [m for m in turn if m["type"] == "chat.looked"]
        assert [[t["name"] for t in m["tracks"]] for m in looked] == [["Sub Bass", "Beat"]]
        assert looked[0]["turn_id"] == turn[-1]["turn_id"]
        assert turn[-1]["usage"]["calls"] == 2
        ws.send_json({"v": 1, "type": "chat.open", "chat_id": chat_id, "request_id": "o"})
        history = until(ws, "chat.history")
    answer = history["turns"][-1]
    assert [(t["index"], t["name"], t["type"]) for t in answer["looked_at"]] == [
        (6, "Sub Bass", "midi"), (2, "Beat", "midi")]
    assert history["turns"][0]["looked_at"] == []


def test_chats_survive_a_restart(setup, tmp_path):
    with TestClient(setup.app) as client, connect(client) as ws:
        hello(ws)
        chat_id = create_chat(ws)
        ws.send_json({"v": 1, "type": "chat.send", "chat_id": chat_id, "text": "hi"})
        collect_turn(ws)
    again = Setup(tmp_path)
    with TestClient(again.app) as client, connect(client) as ws:
        m = hello(ws)
    assert [c["id"] for c in m["chats"]] == [chat_id]


def test_turn_error_carries_the_chat(tmp_path):
    s = Setup(tmp_path, provider=FakeProvider([Reply("Half an", error="overloaded")]))
    with TestClient(s.app) as client, connect(client) as ws:
        hello(ws)
        chat_id = create_chat(ws)
        ws.send_json({"v": 1, "type": "chat.send", "chat_id": chat_id, "text": "hi"})
        turn = collect_turn(ws)
        error = next(m for m in turn if m["type"] == "error")
        assert error["code"] == "provider" and error["message"] == "overloaded"
        assert error["chat_id"] == chat_id and error["turn_id"] == turn[-1]["turn_id"]
        ws.send_json({"v": 1, "type": "chat.open", "chat_id": chat_id})
        answer = until(ws, "chat.history")["turns"][1]
    assert answer["error"] == "overloaded" and answer["warnings"] == []


def test_no_key_is_a_provider_error(tmp_path):
    s = Setup(tmp_path, key=None)
    with TestClient(s.app) as client, connect(client) as ws:
        assert hello(ws)["settings"]["api_key_set"] is False
        chat_id = create_chat(ws)
        ws.send_json({"v": 1, "type": "chat.send", "chat_id": chat_id, "text": "hi",
                      "request_id": "s"})
        m = until(ws, "error")
    assert (m["code"], m["request_id"]) == ("provider", "s")
    assert "API key" in m["message"]


class BlockingProvider(FakeProvider):
    def __init__(self):
        super().__init__([Reply("Done.")])
        self.started = threading.Event()
        self.release = threading.Event()

    def stream_chat(self, system, messages, tools):
        self.started.set()
        self.release.wait(5)
        yield from super().stream_chat(system, messages, tools)


def test_busy_chat_rejects_a_second_send(tmp_path):
    provider = BlockingProvider()
    s = Setup(tmp_path, provider=provider)
    with TestClient(s.app) as client, connect(client) as ws:
        hello(ws)
        chat_id = create_chat(ws)
        ws.send_json({"v": 1, "type": "chat.send", "chat_id": chat_id, "text": "one"})
        assert provider.started.wait(5)
        ws.send_json({"v": 1, "type": "chat.send", "chat_id": chat_id, "text": "two",
                      "request_id": "2"})
        m = until(ws, "error")
        assert (m["code"], m["request_id"]) == ("bad_request", "2")
        provider.release.set()
        collect_turn(ws)


def test_update_and_close(setup):
    with TestClient(setup.app) as client, connect(client) as ws:
        hello(ws)
        chat_id = create_chat(ws)
        ws.send_json({"v": 1, "type": "chat.update", "chat_id": chat_id, "title": "Bass",
                      "context": {"follow_selection": False, "pinned": [1, 3]}})
        m = until(ws, "chat.changed")
        assert m["chat"]["title"] == "Bass"
        assert m["chat"]["context"] == {"follow_selection": False, "pinned": [1, 3]}
        ws.send_json({"v": 1, "type": "chat.close", "chat_id": chat_id, "request_id": "x"})
        m = until(ws, "chat.closed")
    assert (m["chat_id"], m["request_id"]) == (chat_id, "x")
    assert not (setup.store.chats_dir / f"{chat_id}.json").exists()
    assert Chat.load(setup.store.closed_dir / f"{chat_id}.json").title == "Bass"


def test_events_reach_every_client(setup):
    with TestClient(setup.app) as client, connect(client) as a, connect(client) as b:
        hello(a)
        hello(b)
        chat_id = create_chat(a)
        assert until(b, "chat.changed")["chat"]["id"] == chat_id
        a.send_json({"v": 1, "type": "chat.send", "chat_id": chat_id, "text": "hi"})
        assert collect_turn(b)[-1]["chat_id"] == chat_id


# Live


def test_tracks_and_selection(setup):
    with TestClient(setup.app) as client, connect(client) as ws:
        hello(ws)
        ws.send_json({"v": 1, "type": "tracks.list", "request_id": "t"})
        tracks = until(ws, "tracks")["tracks"]
        assert len(tracks) == 8 and tracks[5]["name"] == "Lead Vox"
        setup.live.select(5)
        m = until(ws, "live.status")
        assert m["live"]["selected_track"]["name"] == "Lead Vox"
        setup.live.set_connected(False)
        m = until(ws, "live.status")
        while m["live"]["connected"]:
            m = until(ws, "live.status")
        assert m["live"]["selected_track"] is None and m["live"]["detail"]


def test_example_track_while_live_is_offline(setup):
    setup.live.set_connected(False)
    with TestClient(setup.app) as client, connect(client) as ws:
        assert hello(ws)["live"]["connected"] is False
        ws.send_json({"v": 1, "type": "live.use_example", "name": "Lead Vox"})
        live = until(ws, "live.status")["live"]
        assert live["example"] == "Lead Vox"
        assert live["selected_track"]["name"] == "Lead Vox"
        chat_id = create_chat(ws)
        ws.send_json({"v": 1, "type": "chat.send", "chat_id": chat_id, "text": "wider"})
        collect_turn(ws)
        assert "Lead Vox" in setup.provider.calls[0]["messages"][-1]["content"]
        ws.send_json({"v": 1, "type": "live.use_example", "name": None})
        live = until(ws, "live.status")["live"]
        assert live["example"] is None and live["selected_track"] is None
        ws.send_json({"v": 1, "type": "live.use_example", "name": "Nope", "request_id": "n"})
        assert until(ws, "error")["code"] == "not_found"


# Settings


def test_settings_update_keeps_the_key_in_the_hub(tmp_path):
    s = Setup(tmp_path, key=None)
    with TestClient(s.app) as client, connect(client) as ws:
        hello(ws)
        ws.send_json({"v": 1, "type": "settings.update", "api_key": " sk-new ", "model": "m1",
                      "request_id": "u"})
        raw = ws.receive_text()
    m = json.loads(raw)
    assert m["type"] == "settings" and m["request_id"] == "u"
    assert m["settings"] == {"provider": "claude", "model": "m1", "api_key_set": True,
                             "monthly_budget_usd": None, "default_mode": "teach",
                             "auto_actions": []}
    assert "sk-new" not in raw
    assert s.store.keys.get() == "sk-new"
    assert s.store.load_settings() == StoredSettings(model="m1")


def test_settings_update_saves_the_do_mode_settings(setup):
    with TestClient(setup.app) as client, connect(client) as ws:
        hello(ws)
        ws.send_json({"v": 1, "type": "settings.update", "default_mode": "do",
                      "auto_actions": ["mute", "solo", "mute"]})
        m = until(ws, "settings")
        assert m["settings"]["default_mode"] == "do"
        assert m["settings"]["auto_actions"] == ["mute", "solo"]
        ws.send_json({"v": 1, "type": "settings.update", "auto_actions": []})
        assert until(ws, "settings")["settings"]["auto_actions"] == []
    saved = setup.store.load_settings()
    assert saved.default_mode == "do" and saved.auto_actions == []


def test_messages_this_hub_doesnt_do_yet_are_refused(setup):
    with TestClient(setup.app) as client, connect(client) as ws:
        hello(ws)
        ws.send_json({"v": 1, "type": "voice.transcribe", "request_id": "v1", "audio": "AAAA",
                      "mime": "audio/wav"})
        m = until(ws, "error")
        assert m["code"] == "bad_request" and m["request_id"] == "v1"
        assert "voice.transcribe" in m["message"]
        # The connection is still fine.
        ws.send_json({"v": 1, "type": "tracks.list", "request_id": "t"})
        assert until(ws, "tracks")["request_id"] == "t"


def test_switching_provider_resets_the_model(setup):
    setup.store.save_settings(StoredSettings(model="claude-x"))
    setup.hub.settings = setup.store.load_settings()
    with TestClient(setup.app) as client, connect(client) as ws:
        hello(ws)
        ws.send_json({"v": 1, "type": "settings.update", "provider": "ollama"})
        m = until(ws, "settings")
    assert m["settings"]["provider"] == "ollama" and m["settings"]["model"] == "llama3.1"
    assert setup.store.load_settings().model is None


def test_panel_runs_on_the_model_settings_show(monkeypatch):
    from vibe2knob.hub import make_provider

    monkeypatch.delenv("VIBE2KNOB_MODEL", raising=False)
    assert make_provider(StoredSettings(), "sk").model == "claude-haiku-5-5"
    assert make_provider(StoredSettings(model="claude-sonnet-5-5"), "sk").model == "claude-sonnet-5-5"
    monkeypatch.setenv("VIBE2KNOB_MODEL", "claude-opus-5-5")
    assert make_provider(StoredSettings(), "sk").model == "claude-opus-5-5"


def test_settings_test(setup):
    with TestClient(setup.app) as client, connect(client) as ws:
        hello(ws)
        ws.send_json({"v": 1, "type": "settings.test", "request_id": "t"})
        m = until(ws, "settings")
    assert m["request_id"] == "t"
    assert m["test_result"].startswith("Connected")


def test_usage_reaches_the_tab_the_turn_and_the_report(setup, tmp_path):
    with TestClient(setup.app) as client, connect(client) as ws:
        hello(ws)
        chat_id = create_chat(ws)
        ws.send_json({"v": 1, "type": "chat.send", "chat_id": chat_id, "text": "make it wider"})
        done = collect_turn(ws)[-1]
        assert done["usage"]["model"] == "scripted" and done["usage"]["input_tokens"] > 0
        titled = until(ws, "chat.changed")
        while titled["chat"]["title"] is None:
            titled = until(ws, "chat.changed")
        tab = titled["chat"]
        # The tab's total includes naming the chat.
        assert tab["usage"]["output_tokens"] == done["usage"]["output_tokens"] + 8
        assert tab["title_usage"]["output_tokens"] == 8
        assert tab["context_tokens"] == done["usage"]["input_tokens"] + done["usage"]["output_tokens"]
        ws.send_json({"v": 1, "type": "chat.open", "chat_id": chat_id})
        assert until(ws, "chat.history")["turns"][1]["usage"]["parts"]
        ws.send_json({"v": 1, "type": "usage.get", "range": "7d", "request_id": "u"})
        rep = until(ws, "usage.report")
    assert rep["request_id"] == "u" and len(rep["days"]) == 7
    assert rep["chats"][0]["chat_id"] == chat_id and rep["chats"][0]["title"] == "Wider pad"
    assert {g["key"] for g in rep["by_kind"]} == {"chat", "title"}
    assert rep["totals"]["all"]["cost_usd"] is None  # the scripted model has no price
    assert len(setup.store.usage.read()) == 2
    # A restart reads the ledger back: the tab keeps its total.
    again = Setup(tmp_path)
    with TestClient(again.app) as client, connect(client) as ws:
        m = hello(ws)
    assert m["chats"][0]["usage"]["output_tokens"] == tab["usage"]["output_tokens"]


def test_monthly_budget_warns_after_each_turn(tmp_path):
    # $1.00 spent earlier this month on Sonnet.
    Store(tmp_path).usage.append([UsageRecord(model="claude-sonnet-5-5", input_tokens=500_000)])
    setup = Setup(tmp_path)
    with TestClient(setup.app) as client, connect(client) as ws:
        hello(ws)
        ws.send_json({"v": 1, "type": "settings.update", "monthly_budget_usd": 2.5,
                      "request_id": "b"})
        assert until(ws, "settings")["settings"]["monthly_budget_usd"] == 2.5
        budget = until(ws, "usage.budget")
        assert budget["limit_usd"] == 2.5 and budget["month_cost_usd"] == 1.0
        assert budget["over"] is False
        ws.send_json({"v": 1, "type": "settings.update", "monthly_budget_usd": 0.5})
        assert until(ws, "usage.budget")["over"] is True
        # Every finished turn reports the month again. Nothing stops the turn.
        chat_id = create_chat(ws)
        ws.send_json({"v": 1, "type": "chat.send", "chat_id": chat_id, "text": "make it wider"})
        assert "chat.steps" in [m["type"] for m in collect_turn(ws)]
        assert until(ws, "usage.budget")["over"] is True
    # After a restart, a client hears it right after the welcome.
    setup = Setup(tmp_path)
    assert setup.hub.settings.monthly_budget_usd == 0.5
    # Spend that `vibe2knob eval` writes to the ledger while the hub runs counts too.
    before = setup.hub.budget().month_cost_usd
    setup.store.usage.append([UsageRecord(kind="eval", model="claude-sonnet-5-5", input_tokens=500_000)])
    assert setup.hub.budget().month_cost_usd == round(before + 1.0, 6)
    with TestClient(setup.app) as client, connect(client) as ws:
        hello(ws)
        assert ws.receive_json()["type"] == "usage.budget"
        # 0 or less turns it off, and no budget message follows.
        ws.send_json({"v": 1, "type": "settings.update", "monthly_budget_usd": 0})
        assert until(ws, "settings")["settings"]["monthly_budget_usd"] is None
        ws.send_json({"v": 1, "type": "chat.create", "request_id": "c", "context": {}})
        assert ws.receive_json()["type"] == "chat.changed"
    assert setup.hub.budget() is None


def test_first_run_backfills_the_ledger_from_saved_chats(tmp_path):
    store = Store(tmp_path, keys=MemoryKeys("k"))
    chat = Chat(turns=[])
    chat.turns = Chat.model_validate({"turns": [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "yo", "usage": {"input_tokens": 100, "output_tokens": 50}},
    ]}).turns
    store.save_chat(chat)
    closed = Chat(title="Old one", turns=chat.turns)
    store.save_chat(closed)
    store.close_chat(closed.id)
    hub = Hub(store, None)
    assert len(hub.usage_records) == 2 and all(r.backfilled for r in hub.usage_records)
    assert hub.usage_records[0].model == "claude-opus-5-5"
    tab = hub.summary(hub.chats[chat.id])
    assert tab.usage.input_tokens == 100 and tab.usage.cost_usd > 0
    assert tab.context_window == 1_000_000 and tab.context_tokens == 150
    # A second start doesn't backfill again.
    assert len(Hub(store, None).usage_records) == 2
