"""Check what's in the built wheel and sdist before a release.

    python scripts/check_dist.py dist/ [--version 0.2.0]
"""

import argparse
import sys
import tarfile
import zipfile
from fnmatch import fnmatch
from pathlib import Path

# Each pattern must match at least this many files in the wheel.
WHEEL_NEEDS = {
    "vibe2knob/web/index.html": 1,           # the built panel UI
    "vibe2knob/web/assets/*.js": 1,
    "vibe2knob/knowledge/terms.toml": 1,
    "live_bridge/fixtures/*.json": 8,          # the example tracks behind --fake
    "live_bridge/fixtures/session/*.json": 1,
    "live_bridge/vendor/AbletonOSC/__init__.py": 1,  # what `vibe2knob setup-live` installs
    "live_bridge/vendor/AbletonOSC/manager.py": 1,
    "live_bridge/vendor/AbletonOSC/abletonosc/*.py": 10,
    "live_bridge/vendor/AbletonOSC/pythonosc/*.py": 5,
    "live_bridge/vendor/AbletonOSC/LICENSE.md": 1,
    "*.dist-info/licenses/NOTICE": 1,
}
SDIST_NEEDS = {"*/ui/dist/index.html": 1, "*/live_bridge/vendor/AbletonOSC/manager.py": 1}


def check(names: list[str], needs: dict[str, int], label: str) -> list[str]:
    problems = []
    for pattern, least in needs.items():
        found = sum(fnmatch(n, pattern) for n in names)
        if found < least:
            problems.append(f"{label}: {pattern} matches {found} file(s), needs {least}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", type=Path)
    parser.add_argument("--version", help="The version both files must have.")
    args = parser.parse_args()
    wheels = sorted(args.dist.glob("vibe2knob-*.whl"))
    sdists = sorted(args.dist.glob("vibe2knob-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        print(f"error: expected one wheel and one sdist in {args.dist}, found "
              f"{[p.name for p in wheels + sdists]}")
        return 1
    problems = []
    if args.version:
        for path in (wheels[0], sdists[0]):
            if not path.name.startswith(f"vibe2knob-{args.version}"):
                problems.append(f"{path.name} isn't version {args.version}")
    with zipfile.ZipFile(wheels[0]) as z:
        problems += check(z.namelist(), WHEEL_NEEDS, wheels[0].name)
    with tarfile.open(sdists[0]) as t:
        problems += check(t.getnames(), SDIST_NEEDS, sdists[0].name)
    for p in problems:
        print(f"error: {p}")
    if not problems:
        print(f"{wheels[0].name} and {sdists[0].name} have the panel UI, fixtures and AbletonOSC.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
