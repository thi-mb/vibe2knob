#!/usr/bin/env bash
# Refresh the vendored AbletonOSC (live_bridge/vendor/AbletonOSC) at a pinned commit.
#   scripts/vendor-abletonosc.sh <commit>
# Then set ABLETONOSC_COMMIT in live_bridge/remote_script.py and the commit in NOTICE, and
# check the reads on a real Live (scripts/probe_session_view.py, python3 -m live_bridge).
set -euo pipefail
cd "$(dirname "$0")/.."
commit="${1:?usage: scripts/vendor-abletonosc.sh <commit>}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git clone --quiet https://github.com/ideoforms/AbletonOSC "$tmp/AbletonOSC"
git -C "$tmp/AbletonOSC" checkout --quiet "$commit"
dest=live_bridge/vendor/AbletonOSC
rm -rf "$dest" && mkdir -p "$dest"
# Only what Live runs, plus the license and README.
cp -r "$tmp"/AbletonOSC/{__init__.py,manager.py,abletonosc,pythonosc,LICENSE.md,README.md} "$dest/"
echo "Vendored AbletonOSC $(git -C "$tmp/AbletonOSC" rev-parse HEAD) in $dest"
