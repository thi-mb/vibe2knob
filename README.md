# vibe2knob

An open-source producer mate that sits next to Ableton Live. Tell it how you want your track to
feel ("wider", "trippier", "warmer") and it looks at the selected track's device chain and walks
you through the moves with Live's stock devices, explaining each one. It teaches; you turn the knobs.

> Status: v0.1 in progress. This is the core, a CLI and a bridge that reads the selected track from Live. A chat UI is next.

## Try it

```sh
pip install -e '.[claude]'
export ANTHROPIC_API_KEY=...        # or: ant auth login

vibe2knob ask "make this wider" --state fixtures/synth_pad.json
```

The answer looks like this (shortened):

```text
Spread the pad further across the stereo field ...

1. On Utility > Stereo Width: 120-140 %
   Raises the side signal so the pad spreads wider around the centre.
2. On Chorus-Ensemble > Mode: Ensemble
   ...

Listen: Bypass Utility and listen to how far the pad reaches toward the speakers.
Watch out: Turn on Bass Mono so the low end of the pad stays mono.
```

- `--json` prints the structured steps (device, parameter, value range, explanation).
- `--state -` reads the track state from stdin, so you can pipe the Live bridge into it.
- `--dry-run` prints the exact prompt without calling a model.
- `vibe2knob terms` lists the subjective words it knows.

## Reading the track from Live

`live_bridge/` reads the selected track from Ableton Live through
[AbletonOSC](https://github.com/ideoforms/AbletonOSC), a remote script that exposes Live's
(unofficial) Python API over OSC. Every OSC address the project uses is in
`live_bridge/client.py`, so API changes stay contained there. It needs no extra dependencies.

Setup (Live 11+):

1. Clone AbletonOSC into `~/Music/Ableton/User Library/Remote Scripts/AbletonOSC`.
2. Restart Live, then pick **AbletonOSC** as a Control Surface in
   Settings > Link, Tempo & MIDI.

```sh
python3 -m live_bridge                    # summary + JSON of the selected track
python3 -m live_bridge --json --track 2   # JSON only, for a given track index
python3 -m live_bridge --json | vibe2knob ask "make this wider" --state -
python3 -m live_bridge --summary          # every track, light: names, types, devices, mixer
python3 -m live_bridge --watch            # print connection and selection changes
```

For the hub, `LiveBridge` is the one owner of the connection: a background loop receives
everything on port 11001 and routes each reply to its request or each listener push to its
subscribers, while a watcher pings Live every 3 s and follows the selected track.
`FakeLive` gives the same interface from the files in `fixtures/`, for tests and work
without Live. Writes (mute, solo, parameters) are part of the interface but not built yet.

Known limits: only regular tracks can be read (not returns or master), devices inside racks
aren't listed (only the rack itself and its macros), and a plugin's knobs show up only after
you add them with its Configure button in Live.

## How it works

1. **Track state.** A JSON snapshot of one track: its type, mixer, and device chain with parameter
   values. The format is in [docs/track-state-format.md](docs/track-state-format.md); sample
   tracks are in [`fixtures/`](fixtures/).
2. **Knowledge map.** [`terms.toml`](src/vibe2knob/knowledge/terms.toml) maps subjective words to
   techniques with stock devices, starting values and pitfalls, e.g. "wide" to Utility width,
   chorus, Haas delay and mid/side EQ, with "keep the sub mono".
3. **The model.** The core sends the track, the request and the knowledge map to an LLM and gets
   back structured steps. It then checks the steps against the real chain (stock devices only,
   no "adjust" on a device that isn't there) and flags anything off.

### Models

The model is pluggable (`--provider`):

| Provider | Default model | Setup |
|---|---|---|
| `claude` (default) | `claude-opus-5-5` | `ANTHROPIC_API_KEY` or `ant auth login`. Override with `--model` or `VIBE2KNOB_MODEL`. |
| `ollama` | `llama3.1` | Runs offline with [Ollama](https://ollama.com). `--model` or `VIBE2KNOB_OLLAMA_MODEL`, `OLLAMA_HOST`. |

A new backend is a class with a `generate(system, user, schema) -> str` method, registered in
`src/vibe2knob/providers/__init__.py`.

## Judging the advice

Plausible advice is easy; good advice is the point. [`evals/cases.toml`](evals/cases.toml) holds
test cases: a track, a request, and what a good answer must do. For example, "make the bass
wider" must keep the sub mono, and "make it tighter" on a Drum Rack should go to the Simpler
envelopes first.

```sh
vibe2knob eval                       # all cases, against Claude
vibe2knob eval --only bass-wider --provider ollama
```

Full answers are saved in `evals/reports/` so you can read them, not just the score.

## Contributing

The easiest and most useful contribution needs no code: improve `terms.toml`. Add a word people
use, a better technique, or a pitfall you learned the hard way, and add an eval case for it.
Run `pytest` before opening a PR; it checks that every technique uses stock devices.

```sh
pip install -e '.[dev]'
pytest
```

## Development

The hub is Python (`src/vibe2knob/`) and the panel is React + TypeScript (`ui/`). They talk over
one WebSocket protocol, defined in `src/vibe2knob/protocol.py`. The UI's types are generated from
that file:

```sh
pip install -e '.[dev]' && pytest -q
cd ui && npm ci && npm test && npm run build
cd ui && npm run gen:types   # after changing protocol.py
```

### The panel

After a `git pull`, `scripts/panel.sh` does the rest: it reinstalls the Python package, rebuilds
the UI and opens the panel window (add `--fake` for no Live and no key).

`vibe2knob panel` starts the hub on 127.0.0.1:8765 and opens the panel in your browser. It needs
the `[panel]` extra (`pip install -e '.[panel]'`) and a built UI (`cd ui && npm run build`).

```sh
vibe2knob panel                 # real Live, Claude with the key from Settings or ANTHROPIC_API_KEY
vibe2knob panel --fake          # fixtures instead of Live, a scripted model: no Live, no key
vibe2knob panel --fake --dev    # and work on the UI with hot reload: cd ui && npm run dev
```

Chats and settings live in `~/Library/Application Support/vibe2knob/` (or `$VIBE2KNOB_HOME`); the
API key goes in the Keychain, and an `ANTHROPIC_API_KEY` in the environment or a `.env` file wins.
With `--fake` or `--fake-model`, chats go to a temporary folder instead.

### Panel window

`vibe2knob panel --window` opens the panel in a slim always-on-top window to sit beside Live,
instead of a browser tab. It needs the `[window]` extra (`pip install -e '.[window]'`, which adds
pywebview). It takes the same flags, so `--window --fake` works without Live or a key, and
`--window --dev` shows the Vite dev server.

Closing the window stops the hub, and Ctrl-C in the terminal closes the window. The window's size
and position are saved in `settings.json` and restored on the next launch. **Window > Toggle
Always on Top** turns on-top off or on, and that's remembered too. (With `--fake` the settings go
to a temporary folder, so add `--data-dir` to keep the window's place between fake runs.) If a hub is already running on
the port, a second `vibe2knob panel` says so and exits.

The hub opens the panel at `/?token=...`. A client connects to `/ws?token=...`; a wrong token, Host
or Origin closes the socket with code 4401.
