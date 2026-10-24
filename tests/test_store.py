import json
import os

import pytest

from vibe2knob.chat import Chat
from vibe2knob.store import MemoryKeys, Store, StoredSettings, load_dotenv


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path, keys=MemoryKeys())


def test_chats_round_trip_and_close(store):
    a = Chat(title="A", created_at="2026-10-21T10:00:00+00:00")
    b = Chat(title="B", created_at="2026-10-21T11:00:00+00:00")
    store.save_chat(a)
    store.save_chat(b)
    chats, problems = store.load_chats()
    assert [c.title for c in chats] == ["A", "B"] and problems == []
    store.close_chat(a.id)
    assert [c.id for c in store.load_chats()[0]] == [b.id]
    assert (store.closed_dir / f"{a.id}.json").exists()


def test_unreadable_chats_are_skipped_and_kept(store):
    store.chats_dir.mkdir(parents=True)
    newer = store.chats_dir / "abc.json"
    newer.write_text(json.dumps({"schema_version": 99, "id": "abc"}))
    (store.chats_dir / "bad.json").write_text("{")
    chats, problems = store.load_chats()
    assert chats == [] and len(problems) == 2
    assert newer.exists()


def test_chat_ids_are_checked(store):
    with pytest.raises(ValueError):
        store.close_chat("../settings")


def test_settings(store):
    assert store.load_settings() == StoredSettings()
    store.save_settings(StoredSettings(provider="ollama", model="qwen"))
    data = json.loads(store.settings_path.read_text())
    assert {k: data[k] for k in ("schema_version", "provider", "model")} == {
        "schema_version": 1, "provider": "ollama", "model": "qwen"}
    assert store.load_settings().model == "qwen"
    # A file saved before the budget existed loads with it off.
    assert store.load_settings().monthly_budget_usd is None
    store.save_settings(StoredSettings(monthly_budget_usd=5.0))
    assert store.load_settings().monthly_budget_usd == 5.0
    store.settings_path.write_text(json.dumps({"schema_version": 2}))
    with pytest.raises(ValueError, match="newer"):
        store.load_settings()


def test_env_key_wins(store, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store.set_api_key("sk-keychain")
    assert store.api_key() == "sk-keychain"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert store.api_key() == "sk-env" and store.key_from_env()
    store.set_api_key("")
    assert store.keys.get() is None


def test_load_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("V2K_A", raising=False)
    monkeypatch.setenv("V2K_B", "kept")
    (tmp_path / ".env").write_text('# comment\nexport V2K_A="one"\nV2K_B=two\n')
    load_dotenv(tmp_path / ".env")
    assert os.environ["V2K_A"] == "one" and os.environ["V2K_B"] == "kept"
    monkeypatch.delenv("V2K_A")
