"""The hub on a background thread and `vibe2knob panel --window`, with a stand-in pywebview.

The real window is checked by hand on a Mac; these cover everything around it.
"""

import http.client
import importlib
import json
import socket
import subprocess
import sys
import textwrap
import types
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from live_bridge import FakeLive  # noqa: E402
from vibe2knob import cli  # noqa: E402
from vibe2knob.hub import Hub  # noqa: E402
from vibe2knob.providers.fake import FakeProvider  # noqa: E402
from vibe2knob.server import (  # noqa: E402
    Access,
    HubThread,
    _hub_on,
    _port_free,
    create_app,
    run_panel,
)
from vibe2knob.store import MemoryKeys, Store, WindowState  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StopCountingHub(Hub):
    stopped = 0

    async def stop(self) -> None:
        await super().stop()
        self.stopped += 1


def hub_thread(tmp_path, port):
    store = Store(tmp_path, keys=MemoryKeys())
    hub = StopCountingHub(store, FakeLive(), make_provider=lambda s, k: FakeProvider([]))
    app = create_app(hub, Access.local(port, "tok"), None)
    return HubThread(app, port), hub, store


def get_json(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
        return json.load(r)


def test_hub_thread_starts_and_stops(tmp_path):
    port = free_port()
    thread, hub, _ = hub_thread(tmp_path, port)
    thread.start()
    try:
        assert thread.thread.is_alive()
        assert get_json(port, "/api/hub")["app"] == "vibe2knob"
        assert _hub_on(port)
    finally:
        thread.stop()
    assert not thread.thread.is_alive()
    assert hub.stopped == 1
    assert not _hub_on(port)


def test_the_port_is_free_again_right_after_a_stop(tmp_path):
    # The hub closes the kept-alive connection, which leaves it in TIME_WAIT on our side.
    port = free_port()
    thread, _, _ = hub_thread(tmp_path, port)
    thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    conn.request("GET", "/api/hub")
    conn.getresponse().read()
    thread.stop()
    conn.close()
    assert _port_free(port)
    again, _, _ = hub_thread(tmp_path, port)  # and a new hub really can listen there
    again.start()
    again.stop()


def test_hub_thread_that_cant_start_raises(tmp_path):
    port = free_port()
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", port))
        busy.listen()
        thread, _, _ = hub_thread(tmp_path, port)
        with pytest.raises(RuntimeError):
            thread.start(timeout=5)
        assert not thread.thread.is_alive()


def test_a_running_hub_is_named(tmp_path, capsys):
    port = free_port()
    thread, _, _ = hub_thread(tmp_path / "first", port)
    thread.start()
    try:
        code = run_panel(port, fake_live=True, fake_model=True, open_browser=False)
    finally:
        thread.stop()
    assert code == 2
    assert "already running" in capsys.readouterr().out


def test_another_program_on_the_port(capsys):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert run_panel(port, fake_live=True, fake_model=True, open_browser=False) == 2
    assert "another program" in capsys.readouterr().out


def test_window_without_pywebview_fails_cleanly(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "webview", None)  # makes `import webview` fail
    monkeypatch.delitem(sys.modules, "vibe2knob.window", raising=False)
    assert cli.main(["panel", "--window", "--fake", "--port", str(free_port())]) == 2
    out = capsys.readouterr().out
    assert "pywebview" in out and ".[window]" in out


def test_the_plain_panel_never_imports_pywebview():
    code = "import sys, vibe2knob.cli, vibe2knob.server; print('webview' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


# A stand-in for pywebview: records the window, and start() plays a few events.


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def set(self, *args):
        for h in self.handlers:
            h(*args)


class FakeWindow:
    def __init__(self, title, url, **kwargs):
        self.title, self.url, self.kwargs = title, url, kwargs
        self.on_top = kwargs.get("on_top")
        self.events = types.SimpleNamespace(resized=FakeEvent(), moved=FakeEvent(),
                                            closing=FakeEvent())
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class Screen:
    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height


@pytest.fixture
def fake_webview(monkeypatch):
    webview = types.ModuleType("webview")
    menu = types.ModuleType("webview.menu")
    menu.Menu = lambda title, items: types.SimpleNamespace(title=title, items=items)
    menu.MenuAction = lambda title, fn: types.SimpleNamespace(title=title, function=fn)
    webview.menu = menu
    webview.screens = [Screen(0, 0, 1728, 1117)]
    webview.windows = []
    webview.play = lambda window, start_kwargs: None

    def create_window(title, url, **kwargs):
        webview.windows.append(FakeWindow(title, url, **kwargs))
        return webview.windows[-1]

    def start(**kwargs):
        webview.start_kwargs = kwargs
        webview.play(webview.windows[-1], kwargs)

    webview.create_window, webview.start = create_window, start
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setitem(sys.modules, "webview.menu", menu)
    monkeypatch.delitem(sys.modules, "vibe2knob.window", raising=False)
    window = importlib.import_module("vibe2knob.window")
    monkeypatch.setattr(window, "_ctrl_c_closes", lambda w: None)  # no signals in pytest
    yield webview, window
    sys.modules.pop("vibe2knob.window", None)


def test_window_args_restore_a_position_only_on_a_screen(fake_webview):
    _, window = fake_webview
    screens = [Screen(0, 0, 1728, 1117), Screen(1728, 0, 2560, 1440)]
    args = window.window_args(WindowState(width=420, height=900, x=2000, y=40), screens)
    assert args["x"] == 2000 and args["y"] == 40 and args["width"] == 420
    gone = window.window_args(WindowState(x=5000, y=40), screens)  # that display is unplugged
    assert "x" not in gone and "y" not in gone
    tiny = window.window_args(WindowState(width=100, height=100), screens)
    assert (tiny["width"], tiny["height"]) == window.MIN_SIZE
    assert window.window_args(WindowState(), screens)["on_top"] is True


def test_window_run_saves_size_position_and_on_top(fake_webview, tmp_path):
    webview, window = fake_webview
    port = free_port()
    thread, hub, store = hub_thread(tmp_path, port)

    def play(w, start_kwargs):
        assert _hub_on(port)  # the hub is up before the window shows
        w.events.resized.set(380.0, 700.0)
        w.events.moved.set(1300.0, 25.0)
        [action] = start_kwargs["menu"][0].items
        action.function()  # Window > Toggle Always on Top
        assert w.on_top is False
        w.events.closing.set()

    webview.play = play
    assert window.run(thread, f"http://127.0.0.1:{port}/?token=tok", store) == 0
    w = webview.windows[-1]
    assert w.url.endswith("?token=tok") and w.kwargs["on_top"] is True
    assert w.kwargs["min_size"] == (360, 560) and "x" not in w.kwargs
    assert webview.start_kwargs["private_mode"] is False
    assert not thread.thread.is_alive() and hub.stopped == 1
    saved = store.load_settings().window
    assert (saved.width, saved.height, saved.x, saved.y, saved.on_top) == (380, 700, 1300, 25, False)

    # The next launch opens where it was left.
    thread, _, _ = hub_thread(tmp_path, port)
    webview.play = lambda w, kw: None
    window.run(thread, "http://x/", store)
    again = webview.windows[-1].kwargs
    assert (again["width"], again["x"], again["on_top"]) == (380, 1300, False)


def test_window_state_keeps_other_settings(fake_webview, tmp_path):
    _, window = fake_webview
    store = Store(tmp_path, keys=MemoryKeys())
    store.save_settings(store.load_settings().model_copy(update={"model": "claude-haiku-5-5"}))
    window.save_window_state(store, WindowState(width=500))
    settings = store.load_settings()
    assert settings.model == "claude-haiku-5-5" and settings.window.width == 500


def test_settings_without_a_window_still_load(tmp_path):
    (tmp_path / "settings.json").write_text('{"schema_version": 1, "provider": "claude"}')
    assert Store(tmp_path, keys=MemoryKeys()).load_settings().window == WindowState()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_ctrl_c_closes_the_window(tmp_path):
    # In a child process, since it blocks SIGINT. The stub stands in for pywebview.
    stub = tmp_path / "webview"
    stub.mkdir()
    (stub / "__init__.py").write_text("screens = []\n")
    (stub / "menu.py").write_text("Menu = MenuAction = None\n")
    script = textwrap.dedent("""
        import os, signal, threading, time
        from vibe2knob import window
        window.block_ctrl_c()
        closed = threading.Event()
        class W:
            def destroy(self):
                closed.set()
        window._ctrl_c_closes(W())
        os.kill(os.getpid(), signal.SIGINT)
        print("closed" if closed.wait(5) else "still open")
    """)
    env = {"PYTHONPATH": str(tmp_path), "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                         env=env, cwd=ROOT, timeout=20)
    assert out.stdout.split()[-1] == "closed", out.stderr
