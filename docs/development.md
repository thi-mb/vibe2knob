# Development

One local **hub** (Python, `src/vibe2knob/`) owns the Live connection, the model calls and the
chats. The **panel** (React + TypeScript, `ui/`) is a thin client of the hub. They talk over one
WebSocket protocol, defined in `src/vibe2knob/protocol.py`. `CLAUDE.md` has the map of the code
and the rules.

```sh
pip install -e '.[dev]' && pytest -q          # hub tests
cd ui && npm ci && npm test && npm run build  # UI tests and build
cd ui && npm run gen:types                    # after changing protocol.py
cd ui && npm run dev:fake                     # the panel against a scripted fake hub
cd ui && npm run e2e                          # Playwright journeys + screenshots
```

The UI's types in `ui/src/protocol/` are generated from `protocol.py`, so commit both. CI fails
when they drift.

## From source

You need macOS, Live 11 or later, Python 3.11+ and Node 22 (to build the panel).

```sh
git clone https://github.com/thi-mb/vibe2knob && cd vibe2knob
python3 -m venv .venv
.venv/bin/pip install -e '.[window,dev]'
.venv/bin/vibe2knob setup-live     # AbletonOSC into Live's Remote Scripts, once
scripts/panel.sh
```

`scripts/panel.sh` installs the package, builds the panel and opens it in the window. Run it
again after a `git pull`. If you'd rather have AbletonOSC as a git clone of your own, clone it
into `~/Music/Ableton/User Library/Remote Scripts/AbletonOSC`; `setup-live` leaves a copy it
didn't install alone unless you pass `--force`.

## The panel

After a `git pull`, `scripts/panel.sh` does the rest: it reinstalls the Python package, rebuilds
the UI and opens the panel window (add `--fake` for no Live and no key). It doesn't pull by
itself.

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

The hub opens the panel at `/?token=...`. A client connects to `/ws?token=...`; a wrong token, Host
or Origin closes the socket with code 4401.

### Panel window

`vibe2knob panel --window` opens the panel in a slim always-on-top window to sit beside Live,
instead of a browser tab. It needs the `[window]` extra (`pip install -e '.[window]'`, which adds
pywebview). It takes the same flags, so `--window --fake` works without Live or a key, and
`--window --dev` shows the Vite dev server.

Closing the window stops the hub, and Ctrl-C in the terminal closes the window. The window's size
and position are saved in `settings.json` and restored on the next launch. **Window > Toggle
Always on Top** turns on-top off or on, and that's remembered too. (With `--fake` the settings go
to a temporary folder, so add `--data-dir` to keep the window's place between fake runs.) If a hub
is already running on the port, a second `vibe2knob panel` says so and exits.

## Reading Live

`live_bridge/` reads Live through [AbletonOSC](https://github.com/ideoforms/AbletonOSC), a remote
script that exposes Live's (unofficial) Python API over OSC. Every OSC address the project uses
is in `live_bridge/client.py`, so API changes stay contained there. It needs no extra
dependencies.

```sh
python3 -m live_bridge                    # summary + JSON of the selected track
python3 -m live_bridge --json --track 2   # JSON only, for a given track index
python3 -m live_bridge --summary          # every track, light: names, types, devices, mixer
python3 -m live_bridge --watch            # print connection and selection changes
```

For the hub, `LiveBridge` is the one owner of the connection: a background loop receives
everything on port 11001 and routes each reply to its request or each listener push to its
subscribers, while a watcher pings Live every 3 s and follows the selected track.
`FakeLive` gives the same interface from the files in `fixtures/`, for tests and work without
Live. Writes (mute, solo, parameters) are part of the interface but not built yet.

The JSON for one track is described in [track-state-format.md](track-state-format.md).

### AbletonOSC

`vibe2knob setup-live` installs the copy of AbletonOSC vendored in `live_bridge/vendor/AbletonOSC`,
at the commit pinned in `live_bridge/remote_script.py` (credited in `NOTICE`). It writes a
`.vibe2knob` marker next to it, so a later `setup-live` may update that copy, while someone's own
copy needs `--force` and is moved to `~/Library/Application Support/vibe2knob/backups/` first.
To move to a newer AbletonOSC, run `scripts/vendor-abletonosc.sh <commit>`, update the pin and
`NOTICE`, and check the reads on a real Live before releasing.

## The command line

```sh
pip install -e '.[claude]'
export ANTHROPIC_API_KEY=...

vibe2knob ask "make this wider" --state fixtures/synth_pad.json
vibe2knob chat --state fixtures/synth_pad.json      # multi-turn, in the terminal
```

- `--json` prints the structured steps (device, parameter, value range, explanation).
- `--state -` reads the track state from stdin, so you can pipe the Live bridge into it.
- `--dry-run` prints the exact prompt without calling a model.
- `--provider` picks `claude` (default, `claude-opus-5-5` on the command line) or `ollama`
  (`llama3.1`). `--model`, `VIBE2KNOB_MODEL`, `VIBE2KNOB_OLLAMA_MODEL` and `OLLAMA_HOST`
  override them.
- `vibe2knob terms` lists the subjective words it knows, and `vibe2knob prices` the price table
  behind the cost estimates (`--check` compares it with Anthropic's pricing page).

A new model backend is a class in `src/vibe2knob/providers/`, registered in
`providers/__init__.py`. `providers/fake.py` is a scripted one for tests and keyless runs.

## Judging the advice

Plausible advice is easy; good advice is the point. [`evals/cases.toml`](../evals/cases.toml)
holds test cases: a track, a request, and what a good answer must do. For example, "make the bass
wider" must keep the sub mono, and "make it tighter" on a Drum Rack should go to the Simpler
envelopes first.

```sh
vibe2knob eval --model claude-haiku-5-5          # one-shot answers, all cases
vibe2knob eval --chat --model claude-haiku-5-5   # the chat path, plus chat-only cases
vibe2knob eval --only bass-wider --provider ollama
```

Full answers are saved in `evals/reports/` so you can read them, not just the score. Real-model
runs vary from run to run, so read a failure before trusting it.

For a decision (a prompt change, a new tool), run each case three times and compare with the
baseline. `--repeat 3` reports each case as `passed/3` and shows which checks failed on which run.
`--compare` lists the cases whose pass rate dropped, and fails if the total dropped by more than
one case's worth:

```sh
vibe2knob eval --chat --repeat 3 --model claude-haiku-5-5 \
  --compare evals/reports/baseline-v0.2-chat.json
```

The baseline is the one report kept in git. Eval spend goes to the usage ledger as "Evals", so
Settings › Usage shows it apart from chats.

On the chat path every case also checks that the answer talks about the session as Live shows
it, never about "the summary" or "the context block" it was given.

To see what the knowledge map costs before trimming it, `scripts/measure_knowledge_map.py`
runs a three-turn chat on a small set and on the 39-track set and prints the system prompt's
split (map vs the rest), cache writes and reads per turn, and the map's share of each turn's
cost. It needs a key; `--fake` prints only the split.

## Releasing

Releases go to [PyPI](https://pypi.org/project/vibe2knob/) from GitHub Actions
(`.github/workflows/release.yml`) with trusted publishing, so no token is kept anywhere.

1. **Try it on TestPyPI.** Actions > Release > Run workflow. It builds the UI and the wheel,
   checks the wheel holds the panel, fixtures and AbletonOSC, installs it with pipx on macOS
   and Linux, publishes `X.Y.Z.devN` to [TestPyPI](https://test.pypi.org/project/vibe2knob/),
   then installs that from TestPyPI and checks the panel again.
2. **Release.** Set `__version__` in `src/vibe2knob/__init__.py` (the wheel takes its version
   from there) and merge. Then make the tag on GitHub's site: Releases > Draft a new release,
   choose a tag `v0.2.0` (create it on publish, target `main`), Generate release notes,
   Publish release. Pushing the tag with git works too:

   ```sh
   git checkout main && git pull
   git tag v0.2.0 && git push origin v0.2.0
   ```

   The tag must match `__version__`. The workflow waits for an Approve on the `pypi`
   environment, publishes to PyPI, and adds the wheel and sdist to the GitHub release (making
   one with generated notes if the tag came from git). A version can't be published twice, so a broken release is fixed with
   the next version.

CI's "Package (pip install)" job runs the same wheel checks on every pull request.
