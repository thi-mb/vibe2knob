// A scripted stand-in for the hub, so the panel runs with no Python, no Live and no key:
// `npm run dev:fake`, or `?fake=<scenario>` on any build. It speaks the same protocol over
// a fake socket, and streams answers word by word like the real hub.
//
// Scenarios: "chat" (Live connected, two saved chats), "firstrun" (no key, no chats),
// "offline" (Live not reachable), "empty" (set up, no chats yet), "long" (like "chat", but
// the pad chat has grown long enough for the header to suggest a new one).

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
  Usage,
  UsageReport,
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

// Usage. Prices per million tokens: input, cache write, cache read, output (as pricing.py).
const PRICES: Record<string, [number, number, number, number]> = {
  "claude-haiku-5-5": [0.1, 0.125, 0.01, 0.5],
  "claude-sonnet-5-5": [2, 2.5, 0.1, 10],
};
const INSTRUCTIONS = 7400;
const TOOL_DEFS = 620;
const WINDOW = 1_000_000;

function priced(u: Omit<Usage, "cost_usd" | "cost_complete" | "prompt_cost_usd" | "output_cost_usd">): Usage {
  const p = u.model ? PRICES[u.model] : undefined;
  if (!p) return { ...u, cost_usd: null, cost_complete: false, prompt_cost_usd: null, output_cost_usd: null };
  const prompt = (u.input_tokens * p[0] + u.cache_write_tokens * p[1] + u.cache_read_tokens * p[2]) / 1e6;
  const output = (u.output_tokens * p[3]) / 1e6;
  return { ...u, cost_usd: prompt + output, cost_complete: true, prompt_cost_usd: prompt, output_cost_usd: output };
}

/** One chat answer: the instructions and tools come from the cache, the rest is new. */
function turnUsage(
  sizes: { live: number; history: number; message: number; thinking: number; answer: number; steps: number },
  model = "claude-haiku-5-5",
): Usage {
  const { live, history, message, thinking, answer, steps } = sizes;
  return priced({
    input_tokens: live + history + message,
    cache_read_tokens: INSTRUCTIONS + TOOL_DEFS,
    cache_write_tokens: 0,
    output_tokens: thinking + answer + steps,
    thinking_tokens: thinking,
    calls: 1,
    parts: { instructions: INSTRUCTIONS, tools: TOOL_DEFS, live, history, message, thinking, answer, steps },
    model,
  });
}

function titleUsage(): Usage {
  return priced({ input_tokens: 180, output_tokens: 6, cache_read_tokens: 0, cache_write_tokens: 0, thinking_tokens: 0, calls: 1, parts: {}, model: "claude-haiku-5-5" });
}

function sumUsage(list: Usage[]): Usage {
  const out: Usage = {
    input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, thinking_tokens: 0, calls: 0,
    parts: {}, model: null, cost_usd: null, cost_complete: true, prompt_cost_usd: null, output_cost_usd: null,
  };
  const models = new Set<string | null>();
  for (const u of list) {
    out.input_tokens += u.input_tokens;
    out.output_tokens += u.output_tokens;
    out.cache_read_tokens += u.cache_read_tokens;
    out.cache_write_tokens += u.cache_write_tokens;
    out.thinking_tokens += u.thinking_tokens;
    out.calls += u.calls;
    for (const [k, v] of Object.entries(u.parts)) out.parts[k] = (out.parts[k] ?? 0) + v;
    models.add(u.model);
    if (u.cost_usd !== null) {
      out.cost_usd = (out.cost_usd ?? 0) + u.cost_usd;
      out.prompt_cost_usd = (out.prompt_cost_usd ?? 0) + (u.prompt_cost_usd ?? 0);
      out.output_cost_usd = (out.output_cost_usd ?? 0) + (u.output_cost_usd ?? 0);
    } else out.cost_complete = false;
  }
  out.model = models.size === 1 ? [...models][0] : null;
  return out;
}

const tokensOf = (u: Usage) => u.input_tokens + u.cache_read_tokens + u.cache_write_tokens + u.output_tokens;

interface FakeRecord {
  day: string;
  kind: "chat" | "title" | "ask" | "eval";
  chat_id: string | null;
  usage: Usage;
}

/** The fake hub's "today", so screenshots don't change from day to day. */
const TODAY = "2026-10-21";

function dayOffset(offset: number): string {
  const d = new Date(`${TODAY}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + offset);
  return d.toISOString().slice(0, 10);
}

/** Six weeks of earlier use, from chats long closed. Deterministic, so screenshots are stable. */
function pastRecords(): FakeRecord[] {
  const out: FakeRecord[] = [];
  let seed = 7;
  const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (let d = -44; d < 0; d++) {
    const turns = Math.floor(rand() * 9) * (d % 7 === -1 || d % 7 === -2 ? 2 : 1);
    for (let i = 0; i < turns; i++) {
      const chat = `old-${Math.floor((d + 44) / 3)}`;
      const sonnet = rand() < 0.15;
      out.push({
        day: dayOffset(d), kind: "chat", chat_id: chat,
        usage: turnUsage({
          live: 1500 + Math.floor(rand() * 2500), history: Math.floor(rand() * 6000), message: 12,
          thinking: Math.floor(rand() * 400), answer: 180 + Math.floor(rand() * 220), steps: Math.floor(rand() * 260),
        }, sonnet ? "claude-sonnet-5-5" : "claude-haiku-5-5"),
      });
    }
    if (turns && d % 3 === 0) out.push({ day: dayOffset(d), kind: "title", chat_id: `old-${Math.floor((d + 44) / 3)}`, usage: titleUsage() });
    if (d === -12 || d === -5) {
      out.push({ day: dayOffset(d), kind: "ask", chat_id: null, usage: priced({
        input_tokens: 9800, output_tokens: 700, cache_read_tokens: 0, cache_write_tokens: 0, thinking_tokens: 0, calls: 1, parts: {}, model: "claude-haiku-5-5",
      }) });
    }
  }
  return out;
}

const OLD_TITLES = ["Kick and bass", "Vocal air", "Snare crack", "Wider chorus", "Darker keys", "Riser build", "Tape warmth",
  "Drum bus glue", "Pad space", "Hats too sharp", "Mono check", "Bass growl", "Reverb tail", "Vocal presence", "Lofi keys"];

function now(offsetMinutes = 0): string {
  return new Date(Date.UTC(2026, 9, 21, 20, 0) + offsetMinutes * 60_000).toISOString();
}

interface FakeChat {
  summary: ChatSummary;
  turns: ChatTurn[];
}

const PAD_USAGE = turnUsage({ live: 2140, history: 0, message: 4, thinking: 220, answer: 160, steps: 240 });

function seedChats(long = false): FakeChat[] {
  const padUsage = long
    ? turnUsage({ live: 2300, history: 158_000, message: 6, thinking: 300, answer: 210, steps: 260 })
    : PAD_USAGE;
  const pad: FakeChat = {
    summary: { id: "c-pad", title: "Pad width", context: { follow_selection: true, pinned: [] }, updated_at: now(5),
      usage: null, title_usage: null, context_tokens: null, context_window: null },
    turns: [
      { id: "u-1", role: "user", text: "make it wider", created_at: now(0), steps: null, listen_for: null, caution: null, warnings: [], error: null, usage: null },
      { id: "t-1", role: "assistant", text: WIDER.text, created_at: now(1), steps: WIDER.steps, listen_for: WIDER.listen_for, caution: WIDER.caution, warnings: [], error: null, usage: padUsage },
    ],
  };
  const mud: FakeChat = {
    summary: { id: "c-mud", title: "Low-end mud", context: { follow_selection: true, pinned: [0] }, updated_at: now(2),
      usage: null, title_usage: null, context_tokens: null, context_window: null },
    turns: [
      { id: "u-2", role: "user", text: "the low end feels muddy", created_at: now(-30), steps: null, listen_for: null, caution: null, warnings: [], error: null, usage: null },
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
        usage: turnUsage({ live: 5900, history: 0, message: 6, thinking: 410, answer: 190, steps: 330 }),
      },
    ],
  };
  return [pad, mud];
}

export interface FakeHubOptions {
  scenario?: string;
  /** Milliseconds between streamed words; 0 sends a whole answer at once. */
  wordDelay?: number;
  /** Milliseconds each silent phase (reading, thinking, writing steps) lasts. */
  phaseDelay?: number;
}

/** The fake hub. `socket()` gives a SocketLike for HubClient; the rest are test controls. */
export class FakeHub {
  readonly scenario: string;
  private chats: FakeChat[];
  private live: LiveStatus;
  private settings: Settings;
  private sockets = new Set<FakeSocket>();
  private wordDelay: number;
  private phaseDelay: number;
  private held = false;
  private holdAt = new Set<string>();
  private nextId = 1;
  private records: FakeRecord[];
  private titles = new Map<string, string | null>();

  constructor(options: FakeHubOptions = {}) {
    this.scenario = options.scenario ?? "chat";
    this.wordDelay = options.wordDelay ?? 25;
    this.phaseDelay = options.phaseDelay ?? (this.wordDelay ? 600 : 0);
    const firstRun = this.scenario === "firstrun";
    this.chats = ["chat", "offline", "long"].includes(this.scenario) ? seedChats(this.scenario === "long") : [];
    this.records = this.scenario === "firstrun" || this.scenario === "empty" ? [] : pastRecords();
    for (const r of this.records) if (r.chat_id) this.titles.set(r.chat_id, OLD_TITLES[Number(r.chat_id.slice(4)) % OLD_TITLES.length]);
    for (const chat of this.chats) {
      for (const t of chat.turns) if (t.usage) this.records.push({ day: TODAY, kind: "chat", chat_id: chat.summary.id, usage: t.usage });
      this.records.push({ day: TODAY, kind: "title", chat_id: chat.summary.id, usage: titleUsage() });
      this.refreshSummary(chat);
    }
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

  /** Holds running turns in the phase they're in, or (given phases) as each of them begins. */
  hold(phases?: string[]): void {
    if (phases) this.holdAt = new Set(phases);
    else this.held = true;
  }

  release(all = false): void {
    this.held = false;
    if (all) this.holdAt.clear();
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
        const chat: FakeChat = {
          summary: { id: `c-${this.nextId++}`, title: null, context, updated_at: new Date().toISOString(), usage: null, title_usage: null, context_tokens: null, context_window: null },
          turns: [],
        };
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
      case "usage.get":
        socket.deliver(this.report(m.range ?? "30d", request_id));
        return;
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

  /** A chat's total and how much its next message resends, as the hub works them out. */
  private refreshSummary(chat: FakeChat): void {
    const mine = this.records.filter((r) => r.chat_id === chat.summary.id);
    const records = mine.map((r) => r.usage);
    const titles = mine.filter((r) => r.kind === "title").map((r) => r.usage);
    const last = chat.turns.findLast((t) => t.usage);
    chat.summary = {
      ...chat.summary,
      usage: records.length ? sumUsage(records) : null,
      title_usage: titles.length ? sumUsage(titles) : null,
      context_tokens: last?.usage ? tokensOf(last.usage) : null,
      context_window: last?.usage ? WINDOW : null,
    };
  }

  private report(range: UsageReport["range"], request_id: string | null): UsageReport {
    const days = { today: 1, "7d": 7, "30d": 30, all: null } as const;
    const since = (n: number | null) => (n === null ? "0000-00-00" : dayOffset(1 - n));
    const pick = (n: number | null) => this.records.filter((r) => r.day >= since(n));
    const chosen = pick(days[range]);
    const first = range === "all" ? (chosen.map((r) => r.day).sort()[0] ?? TODAY) : since(days[range]);
    const dayList: string[] = [];
    for (let d = first; d <= TODAY; ) {
      dayList.push(d);
      const next = new Date(`${d}T12:00:00Z`);
      next.setUTCDate(next.getUTCDate() + 1);
      d = next.toISOString().slice(0, 10);
    }
    const group = (key: (r: FakeRecord) => string | null) => {
      const map = new Map<string, Usage[]>();
      for (const r of chosen) {
        const k = key(r);
        if (k !== null) map.set(k, [...(map.get(k) ?? []), r.usage]);
      }
      return [...map.entries()]
        .map(([k, list]) => ({ key: k, usage: sumUsage(list) }))
        .sort((a, b) => (b.usage.cost_usd ?? 0) - (a.usage.cost_usd ?? 0) || tokensOf(b.usage) - tokensOf(a.usage));
    };
    const totals = Object.fromEntries(Object.entries(days).map(([k, n]) => [k, sumUsage(pick(n).map((r) => r.usage))]));
    const t = totals[range];
    const prompt = t.input_tokens + t.cache_read_tokens + t.cache_write_tokens;
    const open = new Set(this.chats.map((c) => c.summary.id));
    return {
      v: 1, type: "usage.report", request_id, range, totals,
      days: dayList.map((d) => ({ date: d, usage: sumUsage(chosen.filter((r) => r.day === d).map((r) => r.usage)) })),
      by_model: group((r) => r.usage.model ?? "unknown"),
      by_kind: group((r) => r.kind),
      chats: group((r) => r.chat_id).map((g) => ({
        chat_id: g.key, open: open.has(g.key), usage: g.usage,
        title: this.chats.find((c) => c.summary.id === g.key)?.summary.title ?? this.titles.get(g.key) ?? null,
      })),
      cache_hit_rate: prompt ? t.cache_read_tokens / prompt : null,
      prices_checked_on: "2026-10-22",
    };
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
    chat.turns.push({ id: `u-${this.nextId++}`, role: "user", text, created_at: at, steps: null, listen_for: null, caution: null, warnings: [], error: null, usage: null });
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
      const history = chat.turns.slice(0, -1).reduce((n, t) => n + Math.ceil(t.text.length / 4) + 60, 0);
      const usage = failing
        ? null
        : turnUsage({
            live: general ? 0 : 2140, history, message: Math.ceil(text.length / 4),
            thinking: 180, answer: Math.ceil(script.text.length / 4), steps: script.steps ? 90 * script.steps.length : 0,
          });
      if (failing) {
        this.broadcast({ v: 1, type: "error", request_id: null, code: "provider", chat_id: chatId, turn_id, message: "The model didn't answer (overloaded). Try again in a moment." });
      } else {
        if (script.steps) {
          this.broadcast({ v: 1, type: "chat.steps", chat_id: chatId, turn_id, steps: script.steps, listen_for: script.listen_for, caution: script.caution, warnings: [] });
        }
        chat.turns.push({ id: turn_id, role: "assistant", text: script.text, created_at: at, steps: script.steps, listen_for: script.listen_for, caution: script.caution, warnings: [], error: null, usage });
        this.records.push({ day: TODAY, kind: "chat", chat_id: chatId, usage: usage! });
      }
      this.broadcast({ v: 1, type: "chat.done", chat_id: chatId, turn_id, usage });
      chat.summary = { ...chat.summary, updated_at: new Date().toISOString() };
      if (!chat.summary.title) {
        chat.summary.title = general ? "Plugin setup" : "Pad width";
        this.records.push({ day: TODAY, kind: "title", chat_id: chatId, usage: titleUsage() });
      }
      this.refreshSummary(chat);
      this.broadcast({ v: 1, type: "chat.changed", request_id: null, chat: chat.summary });
    };
    const status = (phase: "reading" | "thinking" | "steps") => {
      if (this.holdAt.has(phase)) this.held = true;
      this.broadcast({ v: 1, type: "chat.status", chat_id: chatId, turn_id, phase });
    };
    const tick = () => {
      if (i >= words.length) {
        if (failing || !script.steps) return finish();
        status("steps");
        return this.later(this.phaseDelay, finish);
      }
      const chunk = this.wordDelay ? words[i++] : words.slice(i).join("");
      if (!this.wordDelay) i = words.length;
      this.broadcast({ v: 1, type: "chat.delta", chat_id: chatId, turn_id, text: chunk });
      this.later(this.wordDelay, tick);
    };
    status("reading");
    this.later(general ? 0 : this.phaseDelay, () => {
      status("thinking");
      this.later(this.phaseDelay, tick);
    });
  }

  /** setTimeout that waits while the turns are held. */
  private later(ms: number, fn: () => void): void {
    const run = () => (this.held ? setTimeout(run, 50) : fn());
    setTimeout(run, ms);
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
