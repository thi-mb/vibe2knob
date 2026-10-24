"""`vibe2knob doctor`: is everything in place to run the panel against Live?

Checks Python, the panel's packages, the built UI, the API key (set or not, never shown),
AbletonOSC in the Remote Scripts folder, and whether Live answers on OSC.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__

OK, WARN, FAIL, INFO = "ok", "warn", "FAIL", "info"


@dataclass
class Check:
    status: str
    name: str
    detail: str = ""


def _has(*modules: str) -> list[str]:
    """The modules that are missing."""
    return [m for m in modules if importlib.util.find_spec(m) is None]


def check_python() -> Check:
    v = sys.version_info
    status = OK if v >= (3, 11) else FAIL
    return Check(status, f"Python {v.major}.{v.minor}.{v.micro}",
                 "" if status == OK else "vibe2knob needs Python 3.11 or later.")


def check_panel() -> list[Check]:
    missing = _has("fastapi", "uvicorn", "keyring")
    if missing:
        return [Check(FAIL, "Panel packages", f"missing {', '.join(missing)}. Install with "
                      "`pipx install --force 'vibe2knob[window]'`.")]
    checks = [Check(OK, "Panel packages")]
    if _has("webview"):
        checks.append(Check(WARN, "Panel window", "pywebview isn't installed, so `--window` "
                            "won't work (the browser tab does). Install 'vibe2knob[window]'."))
    else:
        checks.append(Check(OK, "Panel window"))
    from .server import find_ui

    ui = find_ui()
    checks.append(Check(OK, "Panel UI", str(ui)) if ui else
                  Check(FAIL, "Panel UI", "not built. In a checkout: cd ui && npm ci && npm run build."))
    return checks


def check_key(store) -> Check:
    if store.load_settings().provider == "ollama":
        return Check(OK, "Model", "Ollama, no key needed")
    if store.key_from_env():
        return Check(OK, "Claude API key", "set, from ANTHROPIC_API_KEY")
    if store.api_key():
        return Check(OK, "Claude API key", "set, in the Keychain")
    return Check(WARN, "Claude API key", "not set yet. Paste it in the panel's setup card, or pick "
                 "Ollama there.")


def check_remote_script(library: Path | None = None) -> Check:
    from live_bridge.remote_script import (ABLETONOSC_COMMIT, DIFFERENT, MISSING, OLDER,
                                           compare, install_dir)

    target = install_dir(library)
    state = compare(target)
    if state == MISSING:
        return Check(FAIL, "AbletonOSC", f"not in {target}. Run `vibe2knob setup-live`.")
    if state == OLDER:
        return Check(WARN, "AbletonOSC", f"{target} holds the version an earlier vibe2knob "
                     "installed. Run `vibe2knob setup-live` to update it, then restart Live.")
    if state == DIFFERENT:
        return Check(WARN, "AbletonOSC", f"{target} holds another version. It may work; "
                     "`vibe2knob setup-live --force` installs the tested one.")
    return Check(OK, "AbletonOSC", f"pinned {ABLETONOSC_COMMIT[:7]}, in {target}")


def check_live(hub_port: int = 8765, bridge_factory=None, hub_running=None) -> Check:
    if hub_running is None:
        try:
            from .server import _hub_on as hub_running
        except ImportError:  # no panel packages, so no panel running either
            hub_running = lambda port: False  # noqa: E731
    if hub_running(hub_port):
        return Check(INFO, "Live", "the panel is running and owns the Live link, so it wasn't "
                     "tested here. The panel's status light shows it.")
    from live_bridge import LiveBridge, LiveError

    try:
        live = (bridge_factory or LiveBridge)(timeout=1.0)
    except OSError as e:
        return Check(FAIL, "Live", f"couldn't open the OSC port: {e}")
    try:
        live.ping()
        version = live.live_version()
    except LiveError:
        return Check(FAIL, "Live", "no answer on OSC port 11000. Is Live open, with AbletonOSC "
                     "picked as a Control Surface in Settings > Link, Tempo & MIDI?")
    finally:
        live.close()
    return Check(OK, "Live", f"Live {version} answers" if version else "answers")


def run_checks(store, library: Path | None = None, hub_port: int = 8765) -> list[Check]:
    checks = [check_python(), *check_panel(), check_key(store)]
    checks.append(check_remote_script(library))
    checks.append(check_live(hub_port))
    return checks


def report(checks: list[Check]) -> str:
    lines = [f"vibe2knob {__version__}"]
    for c in checks:
        lines.append(f"  {c.status:4}  {c.name}" + (f": {c.detail}" if c.detail else ""))
    failed = sum(c.status == FAIL for c in checks)
    lines.append("\nAll set." if not failed else
                 f"\n{failed} problem{'s' if failed > 1 else ''} to fix.")
    return "\n".join(lines)
