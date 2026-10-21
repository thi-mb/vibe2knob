"""The hub's web server: the /ws WebSocket and the built panel UI, on 127.0.0.1 only.

Who may connect: the hub makes a random token at launch and opens the panel at
http://127.0.0.1:<port>/?token=<token>. The UI connects to /ws?token=<token>. A socket with
a wrong or missing token, or a foreign Host or Origin, is accepted and then closed with code
4401 ("token rejected"), so a client can tell it apart from the hub being offline. Plain HTTP
requests with a foreign Host get 403 (that stops DNS rebinding). The UI's files themselves
hold nothing secret, so they're served without the token.

Needs the [panel] extra: pip install 'vibe2knob[panel]'. `--window` also needs the [window]
extra (pywebview); window.py holds that part and is only imported for it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import socket
import tempfile
import threading
import time
import urllib.request
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .hub import Hub
from .protocol import PROTOCOL_VERSION
from .store import MemoryKeys, Store, load_dotenv

log = logging.getLogger("vibe2knob.server")

DEFAULT_PORT = 8765
VITE_PORT = 5173
TOKEN_REJECTED = 4401

PACKAGE_UI = Path(__file__).resolve().parent / "web"  # ui/dist, bundled into the wheel
CHECKOUT_UI = Path(__file__).resolve().parents[2] / "ui" / "dist"

NO_UI = """<!doctype html><meta charset="utf-8"><title>vibe2knob</title>
<body style="font-family: system-ui; background: #111; color: #ddd; padding: 2rem">
<p>The hub is running, but the panel UI isn't built yet.</p>
<p>Run <code>cd ui &amp;&amp; npm ci &amp;&amp; npm run build</code>, then reload, or use
<code>npm run dev</code> with <code>vibe2knob panel --dev</code>.</p></body>"""


@dataclass
class Access:
    """Who may talk to the hub. Phone pairing (v0.5) will replace this, so it's all here."""

    token: str
    hosts: set[str] = field(default_factory=set)  # "host:port" values allowed in Host/Origin

    @classmethod
    def local(cls, port: int, token: str | None = None, dev: bool = False) -> "Access":
        ports = [port] + ([VITE_PORT] if dev else [])
        hosts = {f"{h}:{p}" for h in ("127.0.0.1", "localhost") for p in ports}
        return cls(token or secrets.token_urlsafe(24), hosts)

    def host_ok(self, host: str | None) -> bool:
        return host is not None and host.lower() in self.hosts

    def origin_ok(self, origin: str | None) -> bool:
        if origin is None:  # not a browser (a script or a test client): the token decides
            return True
        parts = urlsplit(origin)
        return parts.scheme in ("http", "https") and self.host_ok(parts.netloc)

    def allows(self, host: str | None, origin: str | None, token: str | None) -> bool:
        return (
            self.host_ok(host)
            and self.origin_ok(origin)
            and token is not None
            and secrets.compare_digest(token, self.token)
        )


class _HostCheck:
    """403 for HTTP requests whose Host isn't ours. WebSockets are checked in /ws."""

    def __init__(self, app, access: Access):
        self.app = app
        self.access = access

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            host = dict(scope["headers"]).get(b"host", b"").decode("latin-1")
            if not self.access.host_ok(host):
                await PlainTextResponse("Forbidden host", status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def find_ui() -> Path | None:
    for path in (PACKAGE_UI, CHECKOUT_UI):
        if (path / "index.html").is_file():
            return path
    return None


def create_app(hub: Hub, access: Access, ui_dir: Path | None = None,
               on_ready=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        await hub.start()
        if on_ready:
            on_ready()
        yield
        await hub.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(_HostCheck, access=access)

    @app.get("/api/hub")
    async def about():
        # Lets a second `vibe2knob panel` tell "a hub is already running" from "port taken".
        return {"app": "vibe2knob", "protocol_version": PROTOCOL_VERSION}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        if not access.allows(websocket.headers.get("host"), websocket.headers.get("origin"),
                             websocket.query_params.get("token")):
            await websocket.close(code=TOKEN_REJECTED, reason="token rejected")
            return
        conn = hub.connect()

        async def write():
            while True:
                await websocket.send_text(await conn.outbox.get())

        writer = asyncio.create_task(write())
        try:
            while True:
                await hub.handle(conn, await websocket.receive_text())
        except WebSocketDisconnect:
            pass
        finally:
            hub.disconnect(conn)
            writer.cancel()

    if ui_dir is not None:
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
    else:
        @app.get("/")
        async def no_ui():
            return HTMLResponse(NO_UI)

    return app


def _port_free(port: int) -> bool:
    """Could the hub listen here? SO_REUSEADDR, as uvicorn binds, so a hub that just quit
    (its closed connections linger in TIME_WAIT for a while) doesn't read as busy. macOS
    lets that bind share a port with a listener on another address, so try connecting first."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _hub_on(port: int) -> bool:
    """Is the program on this port a vibe2knob hub?"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/hub", timeout=1) as r:
            return json.load(r).get("app") == "vibe2knob"
    except (OSError, ValueError, AttributeError):
        return False


def _examples() -> list[dict]:
    from live_bridge.fake import FIXTURES

    return [json.loads(p.read_text()) for p in sorted(FIXTURES.glob("*.json"))]


class HubThread:
    """The hub's server on a background thread, so the window can have the main thread."""

    def __init__(self, app, port: int):
        import uvicorn

        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        self.thread = threading.Thread(target=self._serve, name="vibe2knob-hub", daemon=True)

    def _serve(self) -> None:
        try:
            self.server.run()
        except SystemExit:  # uvicorn exits when it can't start; it has logged why
            pass

    def start(self, timeout: float = 15) -> None:
        """Start serving and wait until it is. Raises RuntimeError if it doesn't come up."""
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                self.stop()
                raise RuntimeError("the hub didn't start (see the messages above)")
            time.sleep(0.02)

    def stop(self, timeout: float = 10) -> None:
        """Stop serving: open sockets close and the hub saves and stops, as on Ctrl-C."""
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout)


def run_panel(port: int = DEFAULT_PORT, *, fake_live: bool = False, fake_model: bool = False,
              data_dir: str | None = None, token: str | None = None, dev: bool = False,
              open_browser: bool = True, window: bool = False) -> int:
    """Start the hub and open the panel, in the browser or (window=True) in its own window.
    Blocks until Ctrl-C, or until the window is closed."""
    import uvicorn
    from live_bridge import FakeLive, LiveBridge

    from .providers.fake import demo_provider

    if window:
        try:
            from . import window as panel_window
        except ImportError as e:
            print(f"error: the panel window needs pywebview ({e.name}): "
                  "pip install -e '.[window]'")
            return 2
        panel_window.block_ctrl_c()
    load_dotenv(Path.cwd() / ".env")
    if not _port_free(port):
        if _hub_on(port):
            print(f"error: a vibe2knob hub is already running on port {port}. Use its window or "
                  "tab, or quit it (Ctrl-C in its terminal) first.")
        else:
            print(f"error: port {port} is in use by another program. Try --port.")
        return 2
    if data_dir is None and fake_model:
        data_dir = tempfile.mkdtemp(prefix="vibe2knob-fake-")  # keep fake chats out of real ones
    store = Store(data_dir, keys=MemoryKeys() if fake_model else None)
    try:
        live = FakeLive() if fake_live else LiveBridge()
    except OSError as e:
        print(f"error: couldn't open the Live link ({e}). Is another vibe2knob running?")
        return 2
    kwargs = {}
    if fake_model:
        demo = demo_provider()
        kwargs["make_provider"] = lambda settings, key: demo
    hub = Hub(store, live, examples=_examples(), make_example=lambda s: FakeLive([s]), **kwargs)
    access = Access.local(port, token, dev)
    url = f"http://127.0.0.1:{port}/?token={access.token}"
    ui_url = f"http://localhost:{VITE_PORT}/?token={access.token}" if dev else url

    def ready():
        print(f"vibe2knob hub on {url}")
        print(f"Data: {store.home}" + (" (fake Live)" if fake_live else "")
              + (" (fake model)" if fake_model else ""))
        if dev:
            print(f"Vite dev UI: {ui_url}")
        if open_browser and not window:
            webbrowser.open(ui_url)

    app = create_app(hub, access, find_ui(), on_ready=ready)
    if not window:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
        return 0
    return panel_window.run(HubThread(app, port), ui_url, store)
