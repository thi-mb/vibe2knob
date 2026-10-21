# vibe2knob

An AI producer mate that sits next to Ableton Live. It reads the session through AbletonOSC and turns subjective asks ("wider") into steps with Live's stock devices. It teaches; the user turns the knobs.

## Shape

One local **hub** (Python) owns the Live connection, the model calls and the chats. Every surface is a thin **client** of the hub: the desktop panel now, a browser tab, and later a phone. Clients only render and send input.

- `src/vibe2knob/`: the hub. `core.py`, `advice.py`, `prompt.py` and `knowledge/` hold the advice engine, `chat.py` runs multi-turn chats (with `tools.py` for the model's tools and `session.py` for what it reads from Live), `providers/` holds the model adapters (`providers/fake.py` is a scripted one for tests and keyless runs), `protocol.py` defines the hub <-> client messages, `hub.py` serves them (with `store.py` for chats, settings and the key), and `server.py` is the web server behind `vibe2knob panel`.
- `live_bridge/`: reads Live through AbletonOSC. Every OSC address is kept in `client.py`.
- `ui/`: the panel, built with React + TypeScript + Vite. `ui/src/protocol/` is generated, so don't edit it by hand.
  `ui/src/hub/fake.ts` is a scripted fake hub for development and the Playwright journeys in `ui/e2e/`.
- `fixtures/`: real track dumps from Live 11. `evals/` holds model eval cases.

## Rules

- **The protocol is the contract.** Change `src/vibe2knob/protocol.py`, then run `npm run gen:types` in `ui/` and commit both. CI fails on drift. Adding optional fields or new message types is fine. Renaming, removing or retyping a field means bumping `PROTOCOL_VERSION`.
- **Version persisted data.** Saved chats and settings carry a `schema_version`, and old files are migrated, never broken.
- **The API key never leaves the hub.** It's kept in the Keychain or the environment, and clients only see `api_key_set`.
- **No telemetry.** The model gets track names, devices and parameters, and never audio.
- **Teach is the default.** Anything that changes Live (the future Do mode) is shown to the user, confirmed and undoable.

## Environment

- Mac only for now, with Live 11 and 12 supported (tested on Live 11). Live 12-only APIs are optional extras.
- v0.1 is for the maintainers only. The repo is private and has no license yet; pick one before it goes public.
- Real-model evals need `ANTHROPIC_API_KEY`, which is only on Thiago's Mac. Pushes go from the cloud because a VPN blocks pushing from the Mac.

## Commands

```sh
pip install -e '.[dev]' && pytest -q          # hub tests
cd ui && npm ci && npm test && npm run build  # UI tests and build
cd ui && npm run gen:types                    # regenerate protocol types after a protocol change
cd ui && npm run dev:fake                     # the panel against a scripted fake hub (no Python, Live or key)
cd ui && npm run e2e                          # Playwright journeys + screenshots against the fake hub
vibe2knob panel --fake --no-browser --token dev   # the hub with fake Live and a scripted model
vibe2knob eval --model claude-haiku-5-5       # real-model evals, one-shot ask path (needs a key)
vibe2knob eval --chat --model claude-haiku-5-5  # the same cases plus chat-only ones, on the chat path
```

Architecture and decisions: see the "vibe2knob architecture" doc in the project, and `docs/future-ideas.md`.
