# vibe2knob

## Live bridge (`live_bridge/`)

Reads state from Ableton Live through [AbletonOSC](https://github.com/ideoforms/AbletonOSC),
a remote script that exposes Live's (unofficial) Python API over OSC. All OSC addresses
the project uses are kept in `live_bridge/client.py`, so API changes stay contained there.
No Python dependencies are needed.

Setup (Live 11+):

1. Clone AbletonOSC into `~/Music/Ableton/User Library/Remote Scripts/AbletonOSC`.
2. Restart Live, then pick **AbletonOSC** as a Control Surface in
   Settings > Link, Tempo & MIDI.

Print the selected track's type, device chain and parameter values:

```sh
python3 -m live_bridge          # summary + JSON
python3 -m live_bridge --json   # JSON only
```

Known limits: only regular tracks can be read (not returns or master), and devices
inside racks aren't listed, only the rack itself and its macros.
