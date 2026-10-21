# Future ideas

Parked ideas that are not part of v0.1. They came up while designing the chat panel (milestone 3).

## Panel and controls

- **Custom accent color.** The default is the amber LED, and the user can pick their own in settings.
- **Turn Live's knobs from the panel.** Link the knob on each step card to the real parameter in Live, so the user can turn it from vibe2knob. The user always does the turning. The model never moves a knob itself.
- **Light theme.** A cream "hardware light" variant, for people who run Live with a light theme.
- **Diagrams in answers.** Not free-form Mermaid or SVG from the model, which streams slowly, reads badly at 400 px and is a safety risk. Instead, a few typed visuals that the model fills through a tool call, like suggest_steps, and that the panel draws as its own cards:
  - A **frequency map** first: a bar per track over 20 Hz to 20 kHz, with the clash zone highlighted, for answers like "these two fight around 2 to 4 kHz".
  - A **signal flow** view: the device chain plus sends and returns. The hub already reads most of it from Live, so the model only points at what matters.

## Context

- **Tracks it looks at on its own.** The model decides to read other tracks (for example Kick and Sub Bass for "the low end is muddy"), and they show as dashed chips. Ship this once the evals show it helps.
- **"Whole mix" context.** Every track with full detail.
- **Context experiments.** Compare focus-only, focus plus session summary, and full session using the evals.

## Voice, control and reach

These came up on 2026-10-21. The hub-and-clients architecture leaves room for each of them.

- **Speech to text** for the chat input. The client records, and the hub transcribes with local Whisper or a provider's speech API. A push-to-talk hotkey that works while Live has focus probably needs a native shell such as Tauri.
- **Do mode (voice control).** For people who already know Live: "solo the guitar", "slow LFO on a high-pass on the vocal group". The agent proposes actions, the user confirms them, and they can be undone. Solo, mute and parameters work through AbletonOSC. Loading devices on Live 11 needs our own remote script. The API can't map an LFO device to a parameter, so use Auto Filter's built-in LFO.
- **More model providers.** OpenAI first, through Sign in with ChatGPT, which lets users spend their plan in partner apps (we'd need access). Each provider is an adapter with its own sign-in method.
- **Phone app.** A PWA served by the hub and paired by QR code over the local network. A relay for reaching it from outside the network can come later.

## v0.1 scope, for reference

- Chats in tabs.
- Context chips: one follows the selection in Live, and @ pins more tracks.
- A light summary of the whole session.
- Settings, with a Claude API key or Ollama.
- A first-run setup card.
- Streamed answers, chats saved locally, and a dark theme only.
