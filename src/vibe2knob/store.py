"""What the hub keeps on disk: chats, settings and the API key.

Everything lives in one folder (by default ~/Library/Application Support/vibe2knob):
- chats/<id>.json: one Chat each (chat.py versions and migrates them),
- chats/closed/: chats whose tab was closed, kept so nothing is lost,
- settings.json: provider, model and monthly budget, and the panel window's size, position and on-top
  switch, with its own schema_version,
- usage.jsonl: the token usage ledger (usage.py), counts only.

The Anthropic key is kept in the Keychain (via keyring), never in these files. An
ANTHROPIC_API_KEY in the environment (or a .env file) wins over the Keychain.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Callable, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from .chat import Chat
from .usage import Ledger
from .protocol import ActionKind, ChatMode

SETTINGS_SCHEMA_VERSION = 1
KEY_ENV = "ANTHROPIC_API_KEY"
KEYRING_SERVICE = "vibe2knob"
KEYRING_USER = "anthropic_api_key"


def default_home() -> Path:
    if os.environ.get("VIBE2KNOB_HOME"):
        return Path(os.environ["VIBE2KNOB_HOME"])
    return Path.home() / "Library" / "Application Support" / "vibe2knob"


class WindowState(BaseModel):
    """The `vibe2knob panel --window` window. Clients never see it: only the window uses it."""

    width: int = 400
    height: int = 860
    x: int | None = None  # None: let the OS place it
    y: int | None = None
    on_top: bool = True


class StoredSettings(BaseModel):
    """settings.json. The client sees protocol.Settings, which adds api_key_set.

    Adding a field with a default needs no new schema_version: older files load as they are.
    """

    schema_version: Literal[1] = SETTINGS_SCHEMA_VERSION
    provider: Literal["claude", "ollama"] = "claude"
    model: str | None = None
    monthly_budget_usd: float | None = None  # None: no budget warning
    default_mode: ChatMode = "teach"
    auto_actions: list[ActionKind] = Field(default_factory=list)
    window: WindowState = Field(default_factory=WindowState)


# One function per old schema_version, each upgrading a dict to the next version.
SETTINGS_MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


def migrate_settings(data: dict) -> dict:
    version = data.get("schema_version", 1)
    if version > SETTINGS_SCHEMA_VERSION:
        raise ValueError(
            f"settings.json was saved by a newer vibe2knob (schema {version}); please update."
        )
    while version < SETTINGS_SCHEMA_VERSION:
        data = SETTINGS_MIGRATIONS[version](data)
        version = data["schema_version"]
    return data


class KeyStore(Protocol):
    def get(self) -> str | None: ...

    def set(self, key: str | None) -> None: ...


class KeyringKeys:
    """The key in the macOS Keychain (or whatever keyring backend is installed)."""

    def get(self) -> str | None:
        try:
            import keyring

            return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception:  # noqa: BLE001 - no keyring backend means no stored key
            return None

    def set(self, key: str | None) -> None:
        try:
            import keyring
        except ImportError as e:
            raise RuntimeError("Saving the key needs keyring: pip install 'vibe2knob[panel]'") from e
        try:
            if key:
                keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
            else:
                try:
                    keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
                except keyring.errors.PasswordDeleteError:
                    pass
        except keyring.errors.KeyringError as e:
            raise RuntimeError(f"Couldn't save the key in the Keychain ({e}).") from e


class MemoryKeys:
    """A key kept in memory only, for tests and fake runs."""

    def __init__(self, key: str | None = None):
        self.key = key

    def get(self) -> str | None:
        return self.key

    def set(self, key: str | None) -> None:
        self.key = key or None


def load_dotenv(path: str | Path) -> None:
    """Read KEY=value lines into os.environ, without overriding what's already set."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Store:
    def __init__(self, home: str | Path | None = None, keys: KeyStore | None = None):
        self.home = Path(home) if home is not None else default_home()
        self.chats_dir = self.home / "chats"
        self.closed_dir = self.chats_dir / "closed"
        self.settings_path = self.home / "settings.json"
        self.usage = Ledger(self.home / "usage.jsonl")
        self.keys = keys if keys is not None else KeyringKeys()

    # Chats

    def _chat_path(self, chat_id: str) -> Path:
        if not chat_id.isalnum():
            raise ValueError(f"Bad chat id {chat_id!r}.")
        return self.chats_dir / f"{chat_id}.json"

    def load_chats(self) -> tuple[list[Chat], list[str]]:
        """Every open chat, oldest first, plus a note for each file that couldn't be read."""
        chats, problems = [], []
        for path in sorted(self.chats_dir.glob("*.json")):
            try:
                chats.append(Chat.load(path))
            except (OSError, ValueError, ValidationError) as e:
                # Leave the file alone: a newer vibe2knob may read it fine.
                problems.append(f"Skipped {path.name}: {e}")
        chats.sort(key=lambda c: c.created_at)
        return chats, problems

    def load_closed_chats(self) -> list[Chat]:
        """Chats whose tab was closed, for the usage report's titles and the backfill."""
        out = []
        for path in sorted(self.closed_dir.glob("*.json")):
            try:
                out.append(Chat.load(path))
            except (OSError, ValueError, ValidationError):
                continue
        return out

    def save_chat(self, chat: Chat) -> None:
        _write_atomic(self._chat_path(chat.id), chat.to_json())

    def close_chat(self, chat_id: str) -> None:
        """Move the chat out of the open list. The file is kept in chats/closed/."""
        path = self._chat_path(chat_id)
        if path.exists():
            self.closed_dir.mkdir(parents=True, exist_ok=True)
            os.replace(path, self.closed_dir / path.name)

    # Settings

    def load_settings(self) -> StoredSettings:
        if not self.settings_path.exists():
            return StoredSettings()
        return StoredSettings.model_validate(
            migrate_settings(json.loads(self.settings_path.read_text()))
        )

    def save_settings(self, settings: StoredSettings) -> None:
        _write_atomic(self.settings_path, settings.model_dump_json(indent=2))

    # The API key

    def api_key(self) -> str | None:
        return os.environ.get(KEY_ENV) or self.keys.get()

    def key_from_env(self) -> bool:
        return bool(os.environ.get(KEY_ENV))

    def set_api_key(self, key: str | None) -> None:
        self.keys.set(key.strip() if key else None)
