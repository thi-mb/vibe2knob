// The panel's state, built only from hub messages plus a few local UI actions. Everything
// here is pure so it can be tested without a browser.

import type {
  ChatSummary,
  ChatTurn,
  HubEnvelope,
  LiveStatus,
  Settings,
  Step,
  TrackRef,
} from "../protocol/types";

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
  view: View;
  testResult: string | null;
  /** The request_id of a chat.create we sent, so its chat.changed opens the new tab. */
  pendingCreate: string | null;
  /** The last error that doesn't belong to a chat. */
  notice: string | null;
}

export const initialState: PanelState = {
  connection: "connecting",
  ready: false,
  hubVersion: null,
  live: { connected: false, selected_track: null, detail: null, example: null },
  settings: { provider: "claude", model: null, api_key_set: false },
  examples: [],
  tracks: [],
  chats: [],
  activeChatId: null,
  turns: {},
  view: "chat",
  testResult: null,
  pendingCreate: null,
  notice: null,
};

export type Action =
  | { type: "hub"; message: HubEnvelope }
  | { type: "connection"; connection: Connection }
  | { type: "select-chat"; chatId: string }
  | { type: "creating-chat"; requestId: string }
  | { type: "user-sent"; chatId: string; requestId: string; text: string; at: string }
  | { type: "show"; view: View }
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
        pendingCreate: null,
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
      return { ...state, chats, turns, activeChatId: active };
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
    case "chat.delta":
      return withTurn(state, m.chat_id, m.turn_id, (t) => ({ ...t, text: t.text + m.text, pending: true }));
    case "chat.steps":
      return withTurn(state, m.chat_id, m.turn_id, (t) => ({
        ...t,
        steps: m.steps,
        listen_for: m.listen_for,
        caution: m.caution,
        warnings: m.warnings,
      }));
    case "chat.done":
      return withTurn(state, m.chat_id, m.turn_id, (t) => ({ ...t, pending: false }));
    case "settings":
      return {
        ...state,
        settings: m.settings,
        testResult: m.test_result ?? state.testResult,
      };
    case "error":
      if (m.chat_id && m.turn_id) {
        return withTurn(state, m.chat_id, m.turn_id, (t) => ({ ...t, error: m.message }));
      }
      return { ...state, notice: m.message };
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
      return { ...state, turns: { ...state.turns, [action.chatId]: [...turns, turn] } };
    }
    case "show":
      return { ...state, view: action.view, testResult: action.view === "settings" ? null : state.testResult };
    case "dismiss-notice":
      return { ...state, notice: null };
  }
}

// Derived views

export function activeChat(state: PanelState): ChatSummary | null {
  return state.chats.find((c) => c.id === state.activeChatId) ?? null;
}

export function isStreaming(state: PanelState, chatId: string | null): boolean {
  if (!chatId) return false;
  return (state.turns[chatId] ?? []).some((t) => t.pending);
}

/** Can the panel read tracks right now (Live, or an example track)? */
export function canSeeLive(state: PanelState): boolean {
  return state.live.connected || state.live.example !== null;
}

/** A chat with no tracks in its context: general questions about Live. */
export function isGeneral(chat: ChatSummary | null): boolean {
  return !!chat && !chat.context.follow_selection && chat.context.pinned.length === 0;
}
