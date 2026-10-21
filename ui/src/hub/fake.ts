// A scripted stand-in for the hub, so the panel runs with no Python, no Live and no key:
// `npm run dev:fake`, or `?fake=<scenario>` on any build. It speaks the same protocol over
// a fake socket, and streams answers word by word like the real hub.
//
// Scenarios: "chat" (Live connected, two saved chats), "firstrun" (no key, no chats),
// "offline" (Live not reachable), "empty" (set up, no chats yet).

import type { SocketLike } from "./client";
import type {
  ChatSummary,
  ChatTurn,
  ClientEnvelope,
  ContextSet,
  HubEnvelope,
  LiveStatus,
  Settings,
  Step,
  TrackRef,
} from "../protocol/types";

const TRACKS: TrackRef[] = [
  { index: 0, name: "Beat", type: "midi", color: 0xff3636, devices: ["Drum Rack"] },
  { index: 1, name: "Sub Bass", type: "midi", color: 0xffa529, devices: ["Operator", "Saturator"] },
  { index: 2, name: "Warm Pad", type: "midi", color: 0xff2a6b, devices: ["Wavetable", "Chorus-Ensemble", "Utility"] },
  { index: 3, name: "Lead Vox", type: "audio", color: 0x5bc0eb, devices: ["EQ Eight", "Compressor", "Reverb"] },
  { index: 4, name: "acoustic-pick", type: "audio", color: 0xb5e655, devices: ["Chorus-Ensemble", "Compressor", "Reverb", "Ozone Imager 2"] },
  { index: 5, name: "Drums", type: "group", color: 0xff3636, devices: ["EQ Eight", "Glue Compressor"] },
  { index: 6, name: "Keys", type: "midi", color: 0xb18cff, devices: ["Electric", "Phaser-Flanger"] },
  { index: 7, name: "FX Riser", type: "audio", color: 0x9aa0a6, devices: [] },
];

const EXAMPLES: TrackRef[] = [TRACKS[2], TRACKS[1], TRACKS[3], TRACKS[5]];

const WIDER: { text: string; steps: Step[]; listen_for: string; caution: string | null } = {
  text:
    "Spread the pad around the kick and vocal instead of sitting in the middle with them. " +
    "You already have Chorus-Ensemble and Utility, so it's two knobs and one optional add.",
  steps: [
    step({ device: "Utility", parameter: "Width", value_range: "120-140 %", on_track: true,
      explanation: "Turns up the side signal so the pad reaches toward the speakers." }),
    step({ device: "Chorus-Ensemble", parameter: "Dry/Wet", value_range: "40-55 % (currently 30 %)", on_track: true,
      explanation: "Each side gets a slightly different copy, which the ear hears as width." }),
    step({ action: "add_device", device: "Utility", parameter: "Bass Mono", value_range: "On, 120 Hz", on_track: false,
      explanation: "Optional. Keeps the pad's low end in the centre so the bass still lands." }),
  ],
  listen_for: "Toggle Utility off and on with the whole mix playing. Does the pad move out from behind the vocal?",
  caution: "Past about 150 % the pad starts to sound phasey in mono. Check it with Utility's Mono switch.",
};

const WASHY = {
  text:
    "The width is fine. The wash comes from the chorus: 85 % wet plus a faster rate makes the " +
    "pitch wobble, and that blurs the chords. Pull the chorus back and keep Utility where it is.",
  steps: [
    step({ device: "Chorus-Ensemble", parameter: "Dry/Wet", value_range: "45-55 % (currently 85 %)", on_track: true,
      explanation: "Keeps enough of the dry pad that the chords stay clear." }),
    step({ device: "Chorus-Ensemble", parameter: "Rate", value_range: "0.5-1 Hz (currently 2.4 Hz)", on_track: true,
      explanation: "A slow rate reads as width. A fast one reads as wobble." }),
  ],
  listen_for: "Hold one chord. Can you still hear each note, or do they smear into one?",
  caution: null,
};

const GENERAL =
  "On a Mac, Live 11 only lists plugins from the formats you've turned on. In **Preferences › Plug-Ins**:\n\n" +
  "1. Turn on *Use Audio Units* and *Use VST3 Plug-In System Folders*.\n" +
  "2. Hold `Option` and click **Rescan**, so Live doesn't skip plugins it gave up on before.";

function step(s: Partial<Step> & Pick<Step, "device" | "explanation">): Step {
  return {
    action: "adjust",
    track: null,
    chain: null,
    on_track: true,
    parameter: null,
    value_range: null,
    stock_device: s.device,
    track_index: null,
    ...s,
  };
}

function now(offsetMinutes = 0): string {
  return new Date(Date.UTC(2026, 9, 21, 20, 0) + offsetMinutes * 60_000).toISOString();
}

interface FakeChat {
  summary: ChatSummary;
  turns: ChatTurn[];
}

function seedChats(): FakeChat[] {
  const pad: FakeChat = {
    summary: { id: "c-pad", title: "Pad width", context: { follow_selection: true, pinned: [] }, updated_at: now(5) },
    turns: [
      { id: "u-1", role: "user", text: "make it wider", created_at: now(0), steps: null, listen_for: null, caution: null, warnings: [], error: null },
      { id: "t-1", role: "assistant", text: WIDER.text, created_at: now(1), steps: WIDER.steps, listen_for: WIDER.listen_for, caution: WIDER.caution, warnings: [], error: null },
    ],
  };
  const mud: FakeChat = {
    summary: { id: "c-mud", title: "Low-end mud", context: { follow_selection: true, pinned: [0] }, updated_at: now(2) },
    turns: [
      { id: "u-2", role: "user", text: "the low end feels muddy", created_at: now(-30), steps: null, listen_for: null, caution: null, warnings: [], error: null },
      {
        id: "t-2", role: "assistant", created_at: now(-29), caution: null,
        text: "Three things fight under 200 Hz: your sub, the kick in Beat, and the left hand of Warm Pad. Start by clearing the pad out, then let the kick duck the sub.",
        steps: [
          step({ action: "add_device", track: "Warm Pad", track_index: 2, device: "EQ Eight", parameter: "Low Cut", value_range: "150-200 Hz", on_track: false,
            explanation: "The pad's lowest notes add weight you can't hear in the mix, only mud." }),
          step({ action: "add_device", track: "Sub Bass", track_index: 1, device: "Compressor", parameter: "Threshold", value_range: "-18 to -24 dB", on_track: false,
            explanation: "Sidechained from Beat, the sub dips on each kick, so they take turns instead of stacking." }),
        ],
        listen_for: "Solo Beat and Sub Bass together. Can you hear each kick land, or does it blur into the bass?",
        warnings: ["Sidechain routing isn't readable through AbletonOSC, so check Audio From on the Compressor yourself."],
        error: null,
      },
    ],
  };
  return [pad, mud];
}

export interface FakeHubOptions {
  scenario?: string;
  /** Milliseconds between streamed words; 0 sends a whole answer at once. */
  wordDelay?: number;
}

/** The fake hub. `socket()` gives a SocketLike for HubClient; the rest are test controls. */
export class FakeHub {
  readonly scenario: string;
  private chats: FakeChat[];
  private live: LiveStatus;
  private settings: Settings;
  private sockets = new Set<FakeSocket>();
  private wordDelay: number;
  private nextId = 1;

  constructor(options: FakeHubOptions = {}) {
    this.scenario = options.scenario ?? "chat";
    this.wordDelay = options.wordDelay ?? 25;
    const firstRun = this.scenario === "firstrun";
    this.chats = this.scenario === "chat" || this.scenario === "offline" ? seedChats() : [];
    this.settings = { provider: "claude", model: "claude-haiku-5-5", api_key_set: !firstRun };
    this.live =
      this.scenario === "offline"
        ? { connected: false, selected_track: null, detail: "AbletonOSC isn't answering on port 11000.", example: null }
        : { connected: true, selected_track: TRACKS[2], detail: null, example: null };
  }

  socket(): SocketLike {
    const s = new FakeSocket(this);
    this.sockets.add(s);
    setTimeout(() => s.open(), 0);
    return s;
  }

  // Test controls (also on window.__fakeHub in the browser)

  select(index: number | null): void {
    if (!this.live.connected && !this.live.example) return;
    this.live = { ...this.live, selected_track: index === null ? null : TRACKS[index] };
    this.broadcast({ v: 1, type: "live.status", live: this.live });
  }

  setLiveConnected(connected: boolean): void {
    this.live = connected
      ? { connected: true, selected_track: TRACKS[2], detail: null, example: null }
      : { connected: false, selected_track: null, detail: "Live isn't running.", example: null };
    this.broadcast({ v: 1, type: "live.status", live: this.live });
  }

  /** Drops every connection, as a hub restart would. */
  drop(code = 1006): void {
    for (const s of this.sockets) s.serverClose(code);
  }

  // Protocol

  /** @internal */
  receive(socket: FakeSocket, m: ClientEnvelope): void {
    const request_id = m.request_id ?? null;
    switch (m.type) {
      case "hello":
        socket.deliver({
          v: 1, type: "welcome", hub_version: "0.1.0-fake",
          chats: this.chats.map((c) => c.summary), live: this.live, settings: this.settings, examples: EXAMPLES,
        });
        return;
      case "tracks.list":
        socket.deliver({ v: 1, type: "tracks", request_id, tracks: this.live.connected ? TRACKS : this.live.example ? EXAMPLES.filter((t) => t.name === this.live.example) : [] });
        return;
      case "chat.create": {
        const context: ContextSet = { follow_selection: m.context?.follow_selection ?? true, pinned: m.context?.pinned ?? [] };
        const chat: FakeChat = { summary: { id: `c-${this.nextId++}`, title: null, context, updated_at: new Date().toISOString() }, turns: [] };
        this.chats.push(chat);
        this.broadcast({ v: 1, type: "chat.changed", request_id, chat: chat.summary });
        return;
      }
      case "chat.open": {
        const chat = this.find(m.chat_id);
        if (!chat) return this.notFound(socket, request_id, m.chat_id);
        socket.deliver({ v: 1, type: "chat.history", request_id, chat_id: m.chat_id, turns: chat.turns });
        return;
      }
      case "chat.update": {
        const chat = this.find(m.chat_id);
        if (!chat) return this.notFound(socket, request_id, m.chat_id);
        chat.summary = {
          ...chat.summary,
          title: m.title ?? chat.summary.title,
          context: m.context ? { follow_selection: m.context.follow_selection ?? true, pinned: m.context.pinned ?? [] } : chat.summary.context,
          updated_at: new Date().toISOString(),
        };
        this.broadcast({ v: 1, type: "chat.changed", request_id, chat: chat.summary });
        return;
      }
      case "chat.close":
        this.chats = this.chats.filter((c) => c.summary.id !== m.chat_id);
        this.broadcast({ v: 1, type: "chat.closed", request_id, chat_id: m.chat_id });
        return;
      case "chat.send":
        this.answer(m.chat_id, m.text, request_id, socket);
        return;
      case "settings.update":
        this.settings = {
          provider: m.provider ?? this.settings.provider,
          model: m.model ?? this.settings.model,
          api_key_set: this.settings.api_key_set || !!m.api_key,
        };
        this.broadcast({ v: 1, type: "settings", request_id, settings: this.settings, test_result: null });
        return;
      case "settings.test": {
        const ok = this.settings.provider === "ollama" || this.settings.api_key_set;
        socket.deliver({
          v: 1, type: "settings", request_id, settings: this.settings,
          test_result: ok ? "Key works" : "No API key saved yet.",
        });
        return;
      }
      case "live.use_example": {
        const example = m.name ? EXAMPLES.find((t) => t.name === m.name) : undefined;
        this.live = example
          ? { connected: false, selected_track: example, detail: this.live.detail, example: example.name }
          : { ...this.live, selected_track: this.live.connected ? this.live.selected_track : null, example: null };
        this.broadcast({ v: 1, type: "live.status", live: this.live });
        return;
      }
    }
  }

  private find(id: string): FakeChat | undefined {
    return this.chats.find((c) => c.summary.id === id);
  }

  private notFound(socket: FakeSocket, request_id: string | null, id: string): void {
    socket.deliver({ v: 1, type: "error", request_id, code: "not_found", message: `No chat ${id}.`, chat_id: null, turn_id: null });
  }

  private answer(chatId: string, text: string, request_id: string | null, socket: FakeSocket): void {
    const chat = this.find(chatId);
    if (!chat) return this.notFound(socket, request_id, chatId);
    const at = new Date().toISOString();
    chat.turns.push({ id: `u-${this.nextId++}`, role: "user", text, created_at: at, steps: null, listen_for: null, caution: null, warnings: [], error: null });
    const turn_id = `t-${this.nextId++}`;
    const general = !chat.summary.context.follow_selection && chat.summary.context.pinned.length === 0;
    const lower = text.toLowerCase();
    const failing = lower.includes("fail");
    const script = general
      ? { text: GENERAL, steps: null, listen_for: null, caution: null }
      : /washy|too much/.test(lower)
        ? WASHY
        : /\bwhy\b/.test(lower)
          ? { text: "Your ears judge width by how different the left and right channels are. Chorus-Ensemble makes delayed copies and bends their pitch differently on each side, and Dry/Wet sets how much of that you hear. Too much and the chords wobble.", steps: null, listen_for: null, caution: null }
          : WIDER;

    const words = (failing ? "Let me look at" : script.text).split(/(?<= )/);
    let i = 0;
    const finish = () => {
      if (failing) {
        this.broadcast({ v: 1, type: "error", request_id: null, code: "provider", chat_id: chatId, turn_id, message: "The model didn't answer (overloaded). Try again in a moment." });
      } else {
        if (script.steps) {
          this.broadcast({ v: 1, type: "chat.steps", chat_id: chatId, turn_id, steps: script.steps, listen_for: script.listen_for, caution: script.caution, warnings: [] });
        }
        chat.turns.push({ id: turn_id, role: "assistant", text: script.text, created_at: at, steps: script.steps, listen_for: script.listen_for, caution: script.caution, warnings: [], error: null });
      }
      this.broadcast({ v: 1, type: "chat.done", chat_id: chatId, turn_id });
      chat.summary = { ...chat.summary, updated_at: new Date().toISOString() };
      if (!chat.summary.title) chat.summary.title = general ? "Plugin setup" : "Pad width";
      this.broadcast({ v: 1, type: "chat.changed", request_id: null, chat: chat.summary });
    };
    const tick = () => {
      if (i >= words.length) return finish();
      const chunk = this.wordDelay ? words[i++] : words.slice(i).join("");
      if (!this.wordDelay) i = words.length;
      this.broadcast({ v: 1, type: "chat.delta", chat_id: chatId, turn_id, text: chunk });
      setTimeout(tick, this.wordDelay);
    };
    setTimeout(tick, this.wordDelay ? 300 : 0);
  }

  private broadcast(m: HubEnvelope): void {
    for (const s of this.sockets) s.deliver(m);
  }

  /** @internal */
  forget(socket: FakeSocket): void {
    this.sockets.delete(socket);
  }
}

class FakeSocket implements SocketLike {
  readyState = 0;
  onopen: SocketLike["onopen"] = null;
  onclose: SocketLike["onclose"] = null;
  onmessage: SocketLike["onmessage"] = null;

  constructor(private readonly hub: FakeHub) {}

  open(): void {
    if (this.readyState !== 0) return;
    this.readyState = 1;
    this.onopen?.({});
  }

  send(data: string): void {
    const m = JSON.parse(data) as ClientEnvelope;
    setTimeout(() => this.hub.receive(this, m), 0);
  }

  deliver(m: HubEnvelope): void {
    if (this.readyState !== 1) return;
    this.onmessage?.({ data: JSON.stringify(m) });
  }

  close(): void {
    this.serverClose(1000);
  }

  serverClose(code: number): void {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.hub.forget(this);
    this.onclose?.({ code });
  }
}
