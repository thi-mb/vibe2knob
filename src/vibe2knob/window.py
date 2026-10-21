"""`vibe2knob panel --window`: the panel in a slim always-on-top window beside Live.

The window gets the main thread (macOS needs that) and the hub runs on a HubThread. Closing
the window stops the hub; Ctrl-C in the terminal closes the window. The window's size,
position and on-top switch are kept in settings.json and restored on the next launch.

Needs the [window] extra: pip install 'vibe2knob[window]'. Importing this module without
pywebview raises ImportError, which run_panel turns into an install hint.
"""

from __future__ import annotations

import signal
import threading

import webview
from webview.menu import Menu, MenuAction

from .store import Store, WindowState

TITLE = "vibe2knob"
MIN_SIZE = (360, 560)


class WindowGeometry:
    """Follows the window's size and position, for saving when it closes."""

    def __init__(self, state: WindowState):
        self.state = state.model_copy()

    def resized(self, width, height) -> None:
        self.state.width, self.state.height = int(width), int(height)

    def moved(self, x, y) -> None:
        self.state.x, self.state.y = int(x), int(y)


def on_a_screen(state: WindowState, screens) -> bool:
    """Is the saved top-left corner on one of today's screens? A display may be gone."""
    if state.x is None or state.y is None:
        return False
    return any(s.x <= state.x < s.x + s.width and s.y <= state.y < s.y + s.height
               for s in screens)


def window_args(state: WindowState, screens) -> dict:
    """create_window keyword arguments for the saved state."""
    args = {
        "width": max(state.width, MIN_SIZE[0]),
        "height": max(state.height, MIN_SIZE[1]),
        "min_size": MIN_SIZE,
        "on_top": state.on_top,
    }
    if on_a_screen(state, screens):
        args["x"], args["y"] = state.x, state.y
    return args


def save_window_state(store: Store, state: WindowState) -> None:
    # Re-read first: the hub may have saved other settings since launch.
    store.save_settings(store.load_settings().model_copy(update={"window": state}))


def _screens() -> list:
    try:
        return webview.screens
    except Exception:  # noqa: BLE001 - no screen list just means the OS places the window
        return []


def block_ctrl_c() -> None:
    """Hold Ctrl-C for _ctrl_c_closes. The GUI loop keeps the main thread in native code, where
    Python signal handlers don't run, so SIGINT is blocked in every thread and one thread
    waits for it. Call before any other thread starts (the Live link starts some), so they
    all inherit the block."""
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})


def _ctrl_c_closes(window) -> None:
    def wait():
        signal.sigwait({signal.SIGINT})
        print()  # past the ^C
        try:
            window.destroy()
        except Exception:  # noqa: BLE001 - it's already closing
            pass

    threading.Thread(target=wait, name="vibe2knob-ctrl-c", daemon=True).start()


def run(hub, url: str, store: Store) -> int:
    """Show the panel at url in a window, with hub (a server.HubThread) behind it. Blocks
    until the window closes, then stops the hub. Call block_ctrl_c() first."""
    geometry = WindowGeometry(store.load_settings().window)
    window = webview.create_window(TITLE, url, **window_args(geometry.state, _screens()),
                                   background_color="#141210")
    window.events.resized += geometry.resized
    window.events.moved += geometry.moved

    def toggle_on_top():
        geometry.state.on_top = not geometry.state.on_top
        window.on_top = geometry.state.on_top

    done = threading.Lock()

    def shutdown():
        # Once, from whichever comes first: the window closing, or webview.start returning.
        # Quit from the app menu ends the process right after `closing`, so it can't wait.
        if done.acquire(blocking=False):
            hub.stop()
            save_window_state(store, geometry.state)

    window.events.closing += shutdown
    _ctrl_c_closes(window)
    try:
        hub.start()
    except RuntimeError as e:
        print(f"error: {e}")
        return 2
    try:
        # private_mode=False keeps the UI's localStorage (theme, first-run setup) across runs.
        webview.start(private_mode=False,
                      menu=[Menu("Window", [MenuAction("Toggle Always on Top", toggle_on_top)])])
    finally:
        shutdown()
    return 0
