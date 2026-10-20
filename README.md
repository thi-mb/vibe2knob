# vibe2knob

An open-source producer mate that sits next to Ableton Live. Tell it how you want your track to
feel ("wider", "trippier", "warmer") and it looks at the selected track's device chain and walks
you through the moves with Live's stock devices, explaining each one. It teaches; you turn the knobs.

> Status: v0.1 in progress. This is the core and a CLI. The Live bridge and a chat UI are next.

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
