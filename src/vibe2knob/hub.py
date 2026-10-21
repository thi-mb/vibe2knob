"""The hub: owns the Live link, the model calls and the chats, and serves every client.

It speaks protocol.py with each connected client, but knows nothing about the transport:
server.py feeds it raw messages and drains each Connection's outbox onto a WebSocket.

Threads: everything here runs on the asyncio loop except
- model turns, which run in a worker pool (ChatEngine.send blocks while it streams),
- Live reads, which run in the same pool (the link serialises them),
- the Live link's own watcher thread, whose callbacks hop onto the loop.
Workers never send to a client directly. They hand messages to `publish`, which queues
them on the loop in order, so a turn's deltas reach every client in the order written.
"""

from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from pydantic import BaseModel, ValidationError

from . import __version__
from .chat import AssistantTurn, Chat, ChatEngine, ChatTitled, TurnDone, TurnError, TurnSteps, TurnText
from .knowledge import Term, load_terms
from .protocol import (
    PROTOCOL_VERSION,
    ChatChanged,
    ChatClose,
    ChatClosed,
    ChatCreate,
    ChatDelta,
    ChatDone,
    ChatSend,
    ChatHistory,
    ChatOpen,
    ChatSteps,
    ChatTurn,
    ChatUpdate,
    ErrorMessage,
    Hello,
    LiveStatus,
    LiveStatusChanged,
    LiveUseExample,
    Settings,
    SettingsChanged,
    SettingsTest,
    SettingsUpdate,
    TrackRef,
    Tracks,
    TracksList,
    Welcome,
    parse_client,
)
from .providers import Provider, ProviderError, get_provider
from .providers.claude import ClaudeProvider
from .store import StoredSettings, Store

log = logging.getLogger("vibe2knob.hub")

CONNECTED = "connected"  # live_bridge.link.CONNECTED; the hub doesn't import live_bridge

ProviderFactory = Callable[[StoredSettings, "str | None"], Provider]
ExampleFactory = Callable[[dict], object]  # a track-state dict -> a LiveLink showing only it


def make_provider(settings: StoredSettings, api_key: str | None) -> Provider:
    """The model the settings ask for. Raises ProviderError when it can't be used."""
    if settings.provider == "claude":
        if not api_key:
            raise ProviderError("No Anthropic API key yet. Add one in Settings.")
        return ClaudeProvider(model=settings.model, api_key=api_key)
    return get_provider(settings.provider, settings.model)


class Connection:
    """One client. The hub puts messages in `outbox`; the transport sends them."""

    def __init__(self, client: str | None = None):
        self.client = client
        self.outbox: asyncio.Queue[str] = asyncio.Queue()

    def send(self, message: BaseModel) -> None:
        self.outbox.put_nowait(message.model_dump_json())


class Hub:
    def __init__(self, store: Store, live=None, *, make_provider: ProviderFactory = make_provider,
                 examples: list[dict] | None = None, make_example: ExampleFactory | None = None,
                 terms: list[Term] | None = None, workers: int = 4):
        """`live` is a live_bridge LiveLink (LiveBridge or FakeLive), or None for no Live.

        `examples` are track-state dicts the user can try while Live is offline;
        `make_example` turns one into a link (live_bridge's FakeLive over that one track).
        """
        self.store = store
        self.live = live
        self.examples = {e["track"]["name"]: e for e in (examples or [])}
        self.make_example = make_example
        self.example: str | None = None
        self.example_live = None
        self.make_provider = make_provider
        self.terms = terms if terms is not None else load_terms()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hub-work")
        self.connections: set[Connection] = set()
        self.chats: dict[str, Chat] = {}
        self.busy: set[str] = set()
        self.settings = store.load_settings()
        self.live_status = LiveStatus(connected=False, detail="Not connected yet.")
        self.loop: asyncio.AbstractEventLoop | None = None
        self._unsubscribe: list[Callable[[], None]] = []
        self._tasks: set[asyncio.Task] = set()
        chats, problems = store.load_chats()
        for problem in problems:
            log.warning(problem)
        self.chats = {c.id: c for c in chats}

    # Lifecycle

    async def start(self) -> None:
        """Attach to the running loop and start watching Live."""
        self.loop = asyncio.get_running_loop()
        if self.live is not None:
            self._unsubscribe = [
                self.live.subscribe_status(lambda *_: self._live_changed()),
                self.live.subscribe_selection(lambda *_: self._live_changed()),
            ]
            self.live.start()
        await self.refresh_live_status()

    async def stop(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        if self.live is not None:
            await asyncio.get_running_loop().run_in_executor(None, self.live.close)
        self.pool.shutdown(wait=False, cancel_futures=True)

    def connect(self, client: str | None = None) -> Connection:
        conn = Connection(client)
        self.connections.add(conn)
        return conn

    def disconnect(self, conn: Connection) -> None:
        self.connections.discard(conn)

    # Sending

    def broadcast(self, message: BaseModel) -> None:
        for conn in list(self.connections):
            conn.send(message)

    def publish(self, message: BaseModel) -> None:
        """Broadcast from any thread, keeping the order messages were published in."""
        assert self.loop is not None, "Hub.start() hasn't run"
        self.loop.call_soon_threadsafe(self.broadcast, message)

    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.pool, fn, *args)

    # Receiving

    async def handle(self, conn: Connection, raw: str | bytes) -> None:
        """Handle one message from a client. Problems go back to it as error messages."""
        try:
            data = json.loads(raw)
        except ValueError:
            conn.send(ErrorMessage(code="bad_request", message="That wasn't JSON."))
            return
        if not isinstance(data, dict):
            conn.send(ErrorMessage(code="bad_request", message="Expected a JSON object."))
            return
        request_id = data.get("request_id") if isinstance(data.get("request_id"), str) else None
        if data.get("v", PROTOCOL_VERSION) != PROTOCOL_VERSION:
            conn.send(ErrorMessage(
                request_id=request_id, code="unsupported_version",
                message=f"This hub speaks protocol v{PROTOCOL_VERSION}, not v{data.get('v')}.",
            ))
            return
        try:
            message = parse_client(raw)
        except ValidationError as e:
            conn.send(ErrorMessage(request_id=request_id, code="bad_request",
                                   message=_short_validation(e)))
            return
        handler = getattr(self, "_on_" + type(message).__name__, None)
        try:
            await handler(conn, message)
        except Exception as e:  # noqa: BLE001 - one bad request must not drop the client
            log.exception("Handling %s failed", type(message).__name__)
            conn.send(ErrorMessage(request_id=request_id, code="internal", message=str(e)))

    async def _on_Hello(self, conn: Connection, m: Hello) -> None:
        conn.client = m.client
        conn.send(Welcome(
            hub_version=__version__,
            chats=[c.summary() for c in self.chats.values()],
            live=self.live_status,
            settings=await self._run(self.settings_view),
            examples=[_track_ref({**e["track"], "index": i}, e["track"].get("devices", []))
                      for i, e in enumerate(self.examples.values())],
        ))

    async def _on_ChatCreate(self, conn: Connection, m: ChatCreate) -> None:
        chat = Chat(context=m.context)
        await self._run(self.store.save_chat, chat)
        self.chats[chat.id] = chat
        self.broadcast(ChatChanged(request_id=m.request_id, chat=chat.summary()))

    async def _on_ChatUpdate(self, conn: Connection, m: ChatUpdate) -> None:
        chat = self._chat(conn, m.chat_id, m.request_id)
        if chat is None:
            return
        if m.title is not None:
            chat.title = m.title.strip()[:60] or None
        if m.context is not None:
            chat.context = m.context
        if chat.id not in self.busy:  # a running turn saves the chat when it ends
            await self._run(self.store.save_chat, chat)
        self.broadcast(ChatChanged(request_id=m.request_id, chat=chat.summary()))

    async def _on_ChatOpen(self, conn: Connection, m: ChatOpen) -> None:
        chat = self._chat(conn, m.chat_id, m.request_id)
        if chat is None:
            return
        turns = [_chat_turn(t) for t in list(chat.turns)]
        conn.send(ChatHistory(request_id=m.request_id, chat_id=chat.id, turns=turns))

    async def _on_ChatClose(self, conn: Connection, m: ChatClose) -> None:
        if self._chat(conn, m.chat_id, m.request_id) is None:
            return
        if m.chat_id in self.busy:
            conn.send(ErrorMessage(request_id=m.request_id, code="bad_request",
                                   message="That chat is still answering."))
            return
        del self.chats[m.chat_id]
        await self._run(self.store.close_chat, m.chat_id)
        self.broadcast(ChatClosed(request_id=m.request_id, chat_id=m.chat_id))

    async def _on_ChatSend(self, conn: Connection, m: ChatSend) -> None:
        chat = self._chat(conn, m.chat_id, m.request_id)
        if chat is None:
            return
        text = m.text.strip()
        if not text:
            conn.send(ErrorMessage(request_id=m.request_id, code="bad_request",
                                   message="The message is empty."))
            return
        if chat.id in self.busy:
            conn.send(ErrorMessage(request_id=m.request_id, code="bad_request",
                                   message="That chat is still answering."))
            return
        try:
            provider = await self._run(self._provider)
        except ProviderError as e:
            conn.send(ErrorMessage(request_id=m.request_id, code="provider", message=str(e)))
            return
        self.busy.add(chat.id)
        self.pool.submit(self._turn, chat, text, provider)

    async def _on_TracksList(self, conn: Connection, m: TracksList) -> None:
        try:
            tracks = await self._run(self._read_tracks)
        except Exception as e:  # noqa: BLE001 - whatever the link raised, Live isn't readable
            conn.send(ErrorMessage(request_id=m.request_id, code="live", message=str(e)))
            return
        conn.send(Tracks(request_id=m.request_id, tracks=tracks))

    async def _on_LiveUseExample(self, conn: Connection, m: LiveUseExample) -> None:
        if m.name is not None and (m.name not in self.examples or self.make_example is None):
            conn.send(ErrorMessage(request_id=m.request_id, code="not_found",
                                   message=f"No example track {m.name!r}."))
            return
        self.example = m.name
        self.example_live = self.make_example(self.examples[m.name]) if m.name else None
        # Every client hears the switch, including the one that asked for it.
        self.live_status = await self._run(self._read_live_status)
        self.broadcast(LiveStatusChanged(live=self.live_status))

    async def _on_SettingsUpdate(self, conn: Connection, m: SettingsUpdate) -> None:
        changes = m.model_dump(exclude_unset=True, include={"provider", "model"})
        if "provider" in changes and changes["provider"] is None:
            del changes["provider"]
        if changes.get("provider", self.settings.provider) != self.settings.provider:
            changes.setdefault("model", None)  # a model name only fits its own provider
        if changes:
            self.settings = self.settings.model_copy(update=changes)
            await self._run(self.store.save_settings, self.settings)
        if m.api_key is not None:
            try:
                await self._run(self.store.set_api_key, m.api_key)
            except RuntimeError as e:
                conn.send(ErrorMessage(request_id=m.request_id, code="internal", message=str(e)))
                return
        self.broadcast(SettingsChanged(request_id=m.request_id,
                                       settings=await self._run(self.settings_view)))

    async def _on_SettingsTest(self, conn: Connection, m: SettingsTest) -> None:
        # A model call can take seconds: answer in the background, so the client's other
        # messages aren't held up behind it.
        task = asyncio.create_task(self._settings_test(conn, m))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _settings_test(self, conn: Connection, m: SettingsTest) -> None:
        result = await self._run(self._test_provider)
        conn.send(SettingsChanged(request_id=m.request_id,
                                  settings=await self._run(self.settings_view),
                                  test_result=result))

    def _chat(self, conn: Connection, chat_id: str, request_id: str | None) -> Chat | None:
        chat = self.chats.get(chat_id)
        if chat is None:
            conn.send(ErrorMessage(request_id=request_id, code="not_found",
                                   message=f"No open chat {chat_id}."))
        return chat

    # Settings and the model

    def settings_view(self) -> Settings:
        return Settings(provider=self.settings.provider, model=self.settings.model,
                        api_key_set=bool(self.store.api_key()))

    def _provider(self) -> Provider:
        return self.make_provider(self.settings, self.store.api_key())

    def _test_provider(self) -> str:
        try:
            reply = self._provider().generate_text(
                "Reply with the single word OK.", "Are you there?"
            )
        except ProviderError as e:
            return f"Failed: {e}"
        except Exception as e:  # noqa: BLE001 - report anything the backend raised
            return f"Failed: {e}"
        return f"Connected: the model replied {reply.strip()[:40]!r}."

    # Turns (run on a worker thread)

    def _turn(self, chat: Chat, text: str, provider: Provider) -> None:
        engine = ChatEngine(provider, self.terms)
        turn_id = None
        try:
            for event in engine.send(chat, text, self._live_view()):
                if isinstance(event, TurnText):
                    turn_id = event.turn_id
                    self.publish(ChatDelta(chat_id=chat.id, turn_id=event.turn_id,
                                           text=event.text))
                elif isinstance(event, TurnSteps):
                    self.publish(ChatSteps(
                        chat_id=chat.id, turn_id=event.turn_id, steps=event.steps,
                        listen_for=event.listen_for, caution=event.caution,
                        warnings=event.warnings,
                    ))
                elif isinstance(event, TurnError):
                    self.publish(ErrorMessage(code="provider", message=event.message,
                                              chat_id=chat.id, turn_id=event.turn_id))
                elif isinstance(event, TurnDone):
                    turn_id = event.turn_id
                    self.store.save_chat(chat)
                    self.publish(ChatDone(chat_id=chat.id, turn_id=event.turn_id))
                    self.publish(ChatChanged(chat=chat.summary()))
                elif isinstance(event, ChatTitled):
                    self.store.save_chat(chat)
                    self.publish(ChatChanged(chat=chat.summary()))
                else:  # TurnStarted: the first delta or steps carry the turn id
                    turn_id = event.turn_id
        except Exception as e:  # noqa: BLE001 - a crashed turn must still end for the client
            log.exception("Turn failed in chat %s", chat.id)
            self.publish(ErrorMessage(code="internal", message=f"The answer failed: {e}",
                                      chat_id=chat.id, turn_id=turn_id))
            if turn_id is not None:
                self.publish(ChatDone(chat_id=chat.id, turn_id=turn_id))
        finally:
            self.loop.call_soon_threadsafe(self.busy.discard, chat.id)

    # Live

    def _live_view(self):
        """The link to read: the example track if one is in use, else Live if it's there.

        None tells the engine Live isn't connected.
        """
        if self.example_live is not None:
            return self.example_live
        if self.live is None or self.live.status != CONNECTED:
            return None
        return self.live

    def _read_tracks(self) -> list[TrackRef]:
        if self._live_view() is None:
            return []
        return [_track_ref(t) for t in self._live_view().session_summary()["tracks"]]

    def _read_live_status(self) -> LiveStatus:
        """`connected` is about Live itself; with an example in use, the example is selected."""
        connected = self.live is not None and self.live.status == CONNECTED
        if self.live is None:
            detail = "No Live link."
        else:
            detail = None if connected else self.live.detail
        link = self._live_view()
        selected = None
        index = link.selected if link is not None else None
        if index is not None:
            try:
                tracks = link.session_summary()["tracks"]
                selected = next((_track_ref(t) for t in tracks if t["index"] == index), None)
            except Exception:  # noqa: BLE001 - leave the chip empty rather than fail
                selected = None
        return LiveStatus(connected=connected, selected_track=selected, detail=detail,
                          example=self.example)

    async def refresh_live_status(self) -> None:
        status = await self._run(self._read_live_status)
        if status != self.live_status:
            self.live_status = status
            self.broadcast(LiveStatusChanged(live=status))

    def _live_changed(self) -> None:
        # Called on the link's watcher thread.
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self.refresh_live_status(), self.loop)


def _track_ref(t: dict, devices: list | None = None) -> TrackRef:
    """From a session-summary track (device names), or a full track with `devices` given."""
    names = [d["name"] for d in devices] if devices is not None else t.get("devices", [])
    return TrackRef(index=t["index"], name=t["name"], type=t["type"], color=t.get("color"),
                    devices=names)


def _chat_turn(turn) -> ChatTurn:
    if isinstance(turn, AssistantTurn):
        return ChatTurn(id=turn.id, role="assistant", text=turn.text, created_at=turn.created_at,
                        steps=turn.steps or None, listen_for=turn.listen_for,
                        caution=turn.caution, warnings=turn.warnings, error=turn.error)
    return ChatTurn(id=turn.id, role="user", text=turn.text, created_at=turn.created_at)


def _short_validation(e: ValidationError) -> str:
    first = e.errors()[0]
    where = ".".join(str(p) for p in first["loc"])
    return f"Bad message: {where}: {first['msg']}" if where else f"Bad message: {first['msg']}"
