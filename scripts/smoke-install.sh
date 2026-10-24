#!/usr/bin/env bash
# Install vibe2knob with pipx into a throwaway place and check that it works: the panel
# serves its UI with --fake, and setup-live installs AbletonOSC. For the release workflow.
#   scripts/smoke-install.sh "dist/vibe2knob-0.2.0-py3-none-any.whl[panel]"
#   scripts/smoke-install.sh "vibe2knob[panel]==0.2.0.dev7" \
#       --pip-args="--index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/"
set -euo pipefail
spec="${1:?usage: scripts/smoke-install.sh <pipx spec> [pipx args...]}"
shift

tmp="$(mktemp -d)"
export PIPX_HOME="$tmp/pipx" PIPX_BIN_DIR="$tmp/bin" HOME="$tmp/home" VIBE2KNOB_HOME="$tmp/data"
mkdir -p "$HOME"
port=8799
cleanup() {
  [[ -n "${hub:-}" ]] && kill "$hub" 2>/dev/null || true
  rm -rf "$tmp"
}
trap cleanup EXIT
cd "$tmp"  # away from the checkout, so nothing imports from it

pipx install "$@" "$spec"
v2k="$PIPX_BIN_DIR/vibe2knob"
"$v2k" --version

# doctor fails here (no Live), but it must run and report.
"$v2k" doctor --port "$port" || true

"$v2k" panel --fake --no-browser --token smoke --port "$port" >"$tmp/panel.log" 2>&1 &
hub=$!
for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$port/api/hub" >/dev/null && break
  kill -0 "$hub" 2>/dev/null || { cat "$tmp/panel.log"; echo "error: the panel quit"; exit 1; }
  sleep 0.5
done
page="$(curl -sf "http://127.0.0.1:$port/?token=smoke")" || { cat "$tmp/panel.log"; exit 1; }
grep -q 'id="root"' <<<"$page" || { echo "error: the panel UI isn't served:"; echo "$page"; exit 1; }
asset="$(grep -o '/assets/index-[^"]*\.js' <<<"$page" | head -1)"
curl -sf -o /dev/null "http://127.0.0.1:$port$asset" || { echo "error: $asset isn't served"; exit 1; }
echo "The panel serves its UI and $asset."

library="$HOME/Music/Ableton/User Library"
mkdir -p "$library"
"$v2k" setup-live
test -f "$library/Remote Scripts/AbletonOSC/manager.py"
"$v2k" setup-live | grep -q "already installed"
echo "setup-live installed AbletonOSC."
