#!/usr/bin/env bash
# Pull, reinstall, rebuild the UI and open the panel, so a checkout never runs stale code.
#   scripts/panel.sh                  # real Live, in the always-on-top window
#   scripts/panel.sh --fake           # any `vibe2knob panel` flags pass through
#   NO_PULL=1 scripts/panel.sh        # skip the git pull (local changes, offline)
set -euo pipefail
cd "$(dirname "$0")/.."

# Use the repo's .venv when there is one, else the python3 on PATH. The panel runs as
# `python -m vibe2knob`, so an older `vibe2knob` elsewhere on PATH can't shadow it.
if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x .venv/bin/python ]]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
fi

[[ -n "${NO_PULL:-}" ]] || git pull --ff-only
"$PYTHON" -m pip install --quiet -e '.[window,dev]'
(cd ui && npm ci --silent && npm run build --silent)

echo "vibe2knob $(git rev-parse --short HEAD) on $(git branch --show-current), python: $PYTHON"
exec "$PYTHON" -m vibe2knob panel --window "$@"
