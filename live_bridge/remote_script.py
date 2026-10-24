"""Installs AbletonOSC, the remote script vibe2knob reads Live through.

A copy of AbletonOSC (MIT, by Daniel John Jones and contributors) is vendored in
vendor/AbletonOSC at a pinned commit, so `vibe2knob setup-live` works offline and every
user runs the version the bridge was tested against. scripts/vendor-abletonosc.sh
refreshes it. In v0.4 this installs our own remote script instead.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

ABLETONOSC_REPO = "https://github.com/ideoforms/AbletonOSC"
ABLETONOSC_COMMIT = "0ca68214bd62c9b5cb641ca34006cfd70ba94430"
VENDORED = Path(__file__).resolve().parent / "vendor" / "AbletonOSC"

# Written next to the copy we install, so a later vibe2knob knows it may update it.
MARKER = ".vibe2knob"

# What Live and AbletonOSC add to the folder at runtime, and what a git clone brings.
_IGNORED = {"__pycache__", "logs", ".git", ".DS_Store", MARKER}

# Same, missing (nothing there), older (a copy an earlier vibe2knob installed), or different
# (someone else's copy).
SAME = "same"
MISSING = "missing"
OLDER = "older"
DIFFERENT = "different"


def user_library() -> Path:
    """Live's default User Library on a Mac."""
    return Path.home() / "Music" / "Ableton" / "User Library"


def install_dir(library: Path | None = None) -> Path:
    return (library or user_library()) / "Remote Scripts" / "AbletonOSC"


def _files(root: Path) -> dict[str, Path]:
    found = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if path.is_file() and not _IGNORED.intersection(rel.parts) and path.suffix != ".pyc":
            found[rel.as_posix()] = path
    return found


def compare(target: Path, source: Path = VENDORED) -> str:
    """Does the folder hold our AbletonOSC? Extra files (a clone's tests, say) don't count."""
    if not target.exists():
        return MISSING
    if not target.is_dir():
        return DIFFERENT
    ours, theirs = _files(source), _files(target)
    for rel, path in ours.items():
        other = theirs.get(rel)
        if other is None or other.read_bytes() != path.read_bytes():
            return OLDER if (target / MARKER).is_file() else DIFFERENT
    return SAME


@dataclass
class InstallResult:
    target: Path
    action: str  # "installed", "unchanged", "updated", "replaced" or "refused"
    backup: Path | None = None


def install(target: Path, backup_dir: Path, force: bool = False,
            source: Path = VENDORED) -> InstallResult:
    """Copy the vendored AbletonOSC to target. A copy an earlier vibe2knob installed is
    updated. Someone else's copy is only replaced with force, and then moved to backup_dir
    first (not left in Remote Scripts, where Live would list it as another control surface)."""
    state = compare(target, source)
    if state == SAME:
        return InstallResult(target, "unchanged")
    backup = None
    if state == OLDER:
        shutil.rmtree(target)
    elif state == DIFFERENT:
        if not force:
            return InstallResult(target, "refused")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"AbletonOSC-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.move(str(target), str(backup))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(*_IGNORED, "*.pyc"))
    (target / MARKER).write_text(f"Installed by vibe2knob setup-live: AbletonOSC "
                                 f"{ABLETONOSC_COMMIT}\n")
    action = {OLDER: "updated", DIFFERENT: "replaced"}.get(state, "installed")
    return InstallResult(target, action, backup)
