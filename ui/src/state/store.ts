// The panel's state, built only from hub messages plus a few local UI actions. Everything
// here is pure so it can be tested without a browser.

import type {
  ChatStatus,
  ChatSummary,
  ChatTurn,
  HubEnvelope,
  LiveStatus,
  Settings,
  Step,
  TrackRef,
  Usage,
  UsageBudget,
  UsageReport,
} from "../protocol/types";

export type UsageRange = UsageReport["range"];

/** What the panel can change in settings.update. */
export interface SettingsChange {
  provider?: "claude" | "ollama";
  model?: string;
  api_key?: string;
  /** 0 turns the limit off. */
  monthly_budget_usd?: number;
}

/** "rejected" is a bad token (relaunch the panel); "offline" is a hub that went away. */
export type Connection = "connecting" | "open" | "offline" | "rejected";

export interface Turn {
  id: string;
  role: "user" | "assistant";
  text: string;
  created_at: string;
  steps: Step[] | null;
  listen_for: string | null;
  caution: string | null;
  warnings: string[];
  /** True while the hub is still streaming this turn. */
  pending: boolean;
  /** Set when the turn failed. */
  error: string | null;
  /** What the answer used; null until chat.done says. */
  usage: Usage | null;
  /** Tracks the model read for this answer, in the order it read them. */
  looked_at: TrackRef[];
}

/** What a running turn is on. "writing" is implied by its prose arriving. */
export type Phase = ChatStatus["phase"] | "writing";

/** A turn in flight, from the moment the user sends until chat.done. */
export interface Progress {
  /** When the user sent (or this client first heard of the turn), for the elapsed timer. */
  since: string;
  phase: Phase;
  /** What the phase is about, e.g. "Reading Kick, Sub Bass", when the hub says. */
  detail: string | null;
  /** The chat.send's request_id, so an error answering it ends the wait. */
  requestId: string | null;
}

export type View = "chat" | "settings";

export interface PanelState {
  connection: Connection;
  /** True once the hub's welcome has arrived on the current connection. */
  ready: boolean;
  hubVersion: string | null;
  live: LiveStatus;
  settings: Settings;
  examples: TrackRef[];
  tracks: TrackRef[];
  /** Tab order. */
  chats: ChatSummary[];
  activeChatId: string | null;
  /** A chat's turns, oldest first; missing until chat.history (or a new chat) fills it. */
  turns: Record<string, Turn[]>;
  /** Turns in flight, by chat. */
  progress: Record<string, Progress>;
  view: View;
  testResult: string | null;
  /** The request_id of a chat.create we sent, so its chat.changed opens the new tab. */
  pendingCreate: string | null;
  /** The last error that doesn't belong to a chat. */
  notice: string | null;
  /** Settings › Usage: the range picked and the hub's latest report for it. */
  usageRange: UsageRange;
  usage: UsageReport | null;
  /** This month's spend against the monthly limit; null while no limit is set. */
  budget: UsageBudget | null;
}

export const initialState: PanelState = {
  connection: "connecting",
  ready: false,
  hubVersion: null,
  live: { connected: false, selected_track: null, detail: null, example: null, can_write: false },
  settings: { provider: "claude", model: null, api_key_set: false, monthly_budget_usd: null, default_mode: "teach", auto_actions: [] },
  examples: [],
  tracks: [],
  chats: [],
  activeChatId: null,
  turns: {},
  progress: {},
  view: "chat",
  testResult: null,
  pendingCreate: null,
  notice: null,
  usageRange: "30d",
  usage: null,
  budget: null,
};

export type Action =
  | { type: "hub"; message: HubEnvelope }
  | { type: "connection"; connection: Connection }
  | { type: "select-chat"; chatId: string }
  | { type: "creating-chat"; requestId: string }
  | { type: "user-sent"; chatId: string; requestId: string; text: string; at: string }
  | { type: "show"; view: View }
  | { type: "usage-range"; range: UsageRange }
  | { type: "dismiss-notice" };

function blankTurn(id: string, role: Turn["role"], at: string): Turn {
  return {
    id,
    role,
    text: "",
    created_at: at,
    steps: null,
    listen_for: null,
    caution: null,
    warnings: [],
    pending: role === "assistant",
    error: null,
    usage: null,
    looked_at: [],
  };
}

function fromHistory(turn: ChatTurn): Turn {
  return {
    id: turn.id,
    role: turn.role,
    text: turn.text,
    created_at: turn.created_at,
    steps: turn.steps,
    listen_for: turn.listen_for,
    caution: turn.caution,
    warnings: turn.warnings,
    pending: false,
    error: turn.error,
    usage: turn.usage,
    looked_at: turn.looked_at ?? [],
  };
}

/** Applies `change` to the assistant turn `turnId` in `chatId`, creating it if needed. */
function withTurn(
  state: PanelState,
  chatId: string,
  turnId: string,
  change: (turn: Turn) => Turn,
): PanelState {
  const turns = state.turns[chatId] ?? [];
  const i = turns.findIndex((t) => t.id === turnId);
  const next =
    i === -1
      ? [...turns, change(blankTurn(turnId, "assistant", new Date().toISOString()))]
      : turns.map((t, j) => (j === i ? change(t) : t));
  return { ...state, turns: { ...state.turns, [chatId]: next } };
}

function withPhase(state: PanelState, chatId: string, phase: Phase, detail: string | null = null): PanelState {
  const was = state.progress[chatId];
  const since = was?.since ?? new Date().toISOString();
  const next: Progress = { since, phase, detail, requestId: was?.requestId ?? null };
  return { ...state, progress: { ...state.progress, [chatId]: next } };
}

/** Adds tracks to what a turn looked at, once each. */
function addLooked(turn: Turn, tracks: TrackRef[]): Turn {
  const seen = new Set(turn.looked_at.map((t) => t.index));
  const fresh = tracks.filter((t) => !seen.has(t.index) && (seen.add(t.index), true));
  return fresh.length ? { ...turn, looked_at: [...turn.looked_at, ...fresh] } : turn;
}

/** Something the user can see arrived, so the progress line steps aside. */
function writing(state: PanelState, chatId: string): PanelState {
  return chatId in state.progress ? withPhase(state, chatId, "writing") : state;
}

function withoutProgress(state: PanelState, chatId: string): PanelState {
  if (!(chatId in state.progress)) return state;
  const { [chatId]: _done, ...progress } = state.progress;
  return { ...state, progress };
}

function upsertChat(chats: ChatSummary[], chat: ChatSummary): ChatSummary[] {
  const i = chats.findIndex((c) => c.id === chat.id);
  return i === -1 ? [...chats, chat] : chats.map((c, j) => (j === i ? chat : c));
}

function mostRecent(chats: ChatSummary[]): string | null {
  if (!chats.length) return null;
  return chats.reduce((a, b) => (b.updated_at > a.updated_at ? b : a)).id;
}

function onHub(state: PanelState, m: HubEnvelope): PanelState {
  switch (m.type) {
    case "welcome": {
      const keep = m.chats.some((c) => c.id === state.activeChatId);
      return {
        ...state,
        ready: true,
        hubVersion: m.hub_version,
        live: m.live,
        settings: m.settings,
        examples: m.examples,
        chats: m.chats,
        activeChatId: keep ? state.activeChatId : mostRecent(m.chats),
        // A new connection may have missed deltas, so every chat reloads its history.
        turns: {},
        // A turn still running on the hub says so again with its next chat.status.
        progress: {},
        pendingCreate: null,
        // The hub sends usage.budget right after the welcome when a limit is set.
        budget: null,
      };
    }
    case "live.status":
      return { ...state, live: m.live };
    case "tracks":
      return { ...state, tracks: m.tracks };
    case "chat.changed": {
      const created = m.request_id !== null && m.request_id === state.pendingCreate;
      return {
        ...state,
        chats: upsertChat(state.chats, m.chat),
        activeChatId: created ? m.chat.id : state.activeChatId,
        turns: created ? { ...state.turns, [m.chat.id]: [] } : state.turns,
        pendingCreate: created ? null : state.pendingCreate,
        view: created ? "chat" : state.view,
      };
    }
    case "chat.closed": {
      const i = state.chats.findIndex((c) => c.id === m.chat_id);
      const chats = state.chats.filter((c) => c.id !== m.chat_id);
      const { [m.chat_id]: _gone, ...turns } = state.turns;
      let active = state.activeChatId;
      if (active === m.chat_id) active = chats[Math.min(i, chats.length - 1)]?.id ?? null;
      return withoutProgress({ ...state, chats, turns, activeChatId: active }, m.chat_id);
    }
    case "chat.history": {
      // Keep a turn that's still streaming if the history was saved before it started.
      const history = m.turns.map(fromHistory);
      const ids = new Set(history.map((t) => t.id));
      const live = (state.turns[m.chat_id] ?? []).filter(
        (t) => !ids.has(t.id) && (t.pending || t.id.startsWith("local-")),
      );
      // The local echo of a user message is replaced once the hub's copy is in history.
      const echoes = live.filter(
        (t) => !t.id.startsWith("local-") || !history.some((h) => h.role === "user" && h.text === t.text),
      );
      return { ...state, turns: { ...state.turns, [m.chat_id]: [...history, ...echoes] } };
    }
    case "chat.status":
      return withPhase(withTurn(state, m.chat_id, m.turn_id, (t) => t), m.chat_id, m.phase, m.detail ?? null);
    case "chat.looked":
      return withTurn(state, m.chat_id, m.turn_id, (t) => addLooked(t, m.tracks));
    case "chat.delta":
      return writing(
        withTurn(state, m.chat_id, m.turn_id, (t) => ({ ...t, text: t.text + m.text, pending: true })),
        m.chat_id,
      );
    case "chat.steps":
      return writing(
        withTurn(state, m.chat_id, m.turn_id, (t) => ({
          ...t,
          steps: m.steps,
          listen_for: m.listen_for,
          caution: m.caution,
          warnings: m.warnings,
        })),
        m.chat_id,
      );
    case "chat.done":
      return withoutProgress(
        withTurn(state, m.chat_id, m.turn_id, (t) => ({ ...t, pending: false, usage: m.usage ?? t.usage })),
        m.chat_id,
      );
    case "usage.budget":
      return { ...state, budget: m };
    case "usage.report":
      return m.range === state.usageRange ? { ...state, usage: m } : state;
    case "settings":
      return {
        ...state,
        settings: m.settings,
        testResult: m.test_result ?? state.testResult,
        budget: m.settings.monthly_budget_usd === null ? null : state.budget,
      };
    case "error": {
      if (m.chat_id && m.turn_id) {
        return withTurn(state, m.chat_id, m.turn_id, (t) => ({ ...t, error: m.message }));
      }
      // A send the hub refused (or a turn that crashed before it began) won't get chat.done.
      const refused = Object.entries(state.progress).find(
        ([id, p]) => id === m.chat_id || (m.request_id !== null && p.requestId === m.request_id),
      );
      const next = refused ? withoutProgress(state, refused[0]) : state;
      return { ...next, notice: m.message };
    }
    case "chat.actions":
    case "voice.transcript":
      return state; // Do mode and voice aren't in the panel yet
  }
}

export function reducer(state: PanelState, action: Action): PanelState {
  switch (action.type) {
    case "hub":
      return onHub(state, action.message);
    case "connection":
      return {
        ...state,
        connection: action.connection,
        ready: action.connection === "open" ? state.ready : false,
      };
    case "select-chat":
      return { ...state, activeChatId: action.chatId, view: "chat" };
    case "creating-chat":
      return { ...state, pendingCreate: action.requestId };
    case "user-sent": {
      const turn: Turn = { ...blankTurn(`local-${action.requestId}`, "user", action.at), text: action.text };
      const turns = state.turns[action.chatId] ?? [];
      return {
        ...state,
        turns: { ...state.turns, [action.chatId]: [...turns, turn] },
        progress: {
          ...state.progress,
          [action.chatId]: { since: action.at, phase: "reading", detail: null, requestId: action.requestId },
        },
      };
    }
    case "show":
      return { ...state, view: action.view, testResult: action.view === "settings" ? null : state.testResult };
    case "dismiss-notice":
      return { ...state, notice: null };
    case "usage-range":
      return { ...state, usageRange: action.range };
  }
}

// Derived views

export function activeChat(state: PanelState): ChatSummary | null {
  return state.chats.find((c) => c.id === state.activeChatId) ?? null;
}

export function isStreaming(state: PanelState, chatId: string | null): boolean {
  if (!chatId) return false;
  return chatId in state.progress || (state.turns[chatId] ?? []).some((t) => t.pending);
}

/** Can the panel read tracks right now (Live, or an example track)? */
export function canSeeLive(state: PanelState): boolean {
  return state.live.connected || state.live.example !== null;
}

/** A chat with no tracks in its context: general questions about Live. */
export function isGeneral(chat: ChatSummary | null): boolean {
  return !!chat && !chat.context.follow_selection && chat.context.pinned.length === 0;
}

/** The month a budget warning belongs to, as "YYYY-MM" in local time. */
export function monthKey(at: Date = new Date()): string {
  return `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}`;
}

/** Show the budget banner: over the limit, and not yet dismissed this month. */
export function showBudgetBanner(state: PanelState, dismissedMonth: string | null, month = monthKey()): boolean {
  return !!state.budget?.over && dismissedMonth !== month;
}
