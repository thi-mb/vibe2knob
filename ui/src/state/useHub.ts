import { useEffect, useMemo, useReducer, useRef } from "react";
import { HubClient, TOKEN_REJECTED } from "../hub/client";
import { hubTarget } from "../hub/connect";
import { FakeHub } from "../hub/fake";
import type { ClientEnvelope, ContextSet, HubEnvelope } from "../protocol/types";
import { initialState, reducer, type PanelState, type UsageRange, type View } from "./store";

export interface HubActions {
  newChat(context?: ContextSet): void;
  selectChat(chatId: string): void;
  closeChat(chatId: string): void;
  setContext(chatId: string, context: ContextSet): void;
  /** Sends a message in the active chat, creating a chat first if there is none. */
  send(text: string): void;
  listTracks(): void;
  updateSettings(change: { provider?: "claude" | "ollama"; model?: string; api_key?: string }): void;
  testSettings(): void;
  useExample(name: string | null): void;
  show(view: View): void;
  dismissNotice(): void;
  /** Asks the hub for the usage report (Settings › Usage), for a new range or afresh. */
  loadUsage(range?: UsageRange): void;
}

const RETRY_MS = [500, 1000, 2000, 4000];

declare global {
  interface Window {
    __fakeHub?: FakeHub;
  }
}

export function useHub(): [PanelState, HubActions] {
  const [state, dispatch] = useReducer(reducer, initialState);
  const stateRef = useRef(state);
  stateRef.current = state;
  const clientRef = useRef<HubClient | null>(null);
  // Text typed before there was a chat to send it to, keyed by the chat.create request.
  const queued = useRef(new Map<string, string>());

  useEffect(() => {
    const target = hubTarget();
    let makeSocket: ConstructorParameters<typeof HubClient>[1];
    if (target.fake) {
      const fake = new FakeHub({ scenario: target.fake });
      window.__fakeHub = fake;
      makeSocket = () => fake.socket();
    }
    const client = new HubClient(target.url, makeSocket);
    clientRef.current = client;
    let attempts = 0;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const offHandlers: Array<() => void> = [];
    const route = (m: HubEnvelope) => {
      dispatch({ type: "hub", message: m });
      if (m.type === "chat.changed" && m.request_id && queued.current.has(m.request_id)) {
        const text = queued.current.get(m.request_id)!;
        queued.current.delete(m.request_id);
        const id = trySend(client, { type: "chat.send", chat_id: m.chat.id, text });
        if (id) dispatch({ type: "user-sent", chatId: m.chat.id, requestId: id, text, at: new Date().toISOString() });
      }
    };
    const types: HubEnvelope["type"][] = [
      "welcome", "live.status", "tracks", "chat.changed", "chat.closed", "chat.history",
      "chat.status", "chat.delta", "chat.steps", "chat.done", "settings", "usage.report", "error",
    ];
    for (const t of types) offHandlers.push(client.on(t, route));
    offHandlers.push(
      client.onOpen(() => {
        attempts = 0;
        dispatch({ type: "connection", connection: "open" });
        client.send({ type: "hello", client: "browser" });
        client.send({ type: "tracks.list" });
      }),
      client.onClose((code) => {
        if (stopped) return;
        if (code === TOKEN_REJECTED) {
          dispatch({ type: "connection", connection: "rejected" });
          return;
        }
        dispatch({ type: "connection", connection: attempts === 0 ? "connecting" : "offline" });
        retry = setTimeout(connect, RETRY_MS[Math.min(attempts++, RETRY_MS.length - 1)]);
      }),
    );
    const connect = () => {
      try {
        client.connect();
      } catch {
        dispatch({ type: "connection", connection: "offline" });
        retry = setTimeout(connect, RETRY_MS[Math.min(attempts++, RETRY_MS.length - 1)]);
      }
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      offHandlers.forEach((off) => off());
      client.close();
    };
  }, []);

  // Load the history of whichever chat is shown, once per connection.
  const active = state.activeChatId;
  const loaded = active !== null && state.turns[active] !== undefined;
  useEffect(() => {
    if (state.ready && active && !loaded) trySend(clientRef.current, { type: "chat.open", chat_id: active });
  }, [state.ready, active, loaded]);

  // Ask for the track list again whenever Live (or the example in use) changes.
  const liveKey = `${state.live.connected}|${state.live.example}`;
  useEffect(() => {
    if (state.ready) trySend(clientRef.current, { type: "tracks.list" });
  }, [state.ready, liveKey]);

  const actions = useMemo<HubActions>(() => {
    const client = () => clientRef.current;
    return {
      newChat(context = { follow_selection: true, pinned: [] }) {
        const id = trySend(client(), { type: "chat.create", context });
        if (id) dispatch({ type: "creating-chat", requestId: id });
      },
      selectChat(chatId) {
        dispatch({ type: "select-chat", chatId });
      },
      closeChat(chatId) {
        trySend(client(), { type: "chat.close", chat_id: chatId });
      },
      setContext(chatId, context) {
        trySend(client(), { type: "chat.update", chat_id: chatId, context });
      },
      send(text) {
        const c = client();
        const chatId = stateRef.current.activeChatId;
        if (!c) return;
        if (chatId) {
          const id = trySend(c, { type: "chat.send", chat_id: chatId, text });
          if (id) dispatch({ type: "user-sent", chatId, requestId: id, text, at: new Date().toISOString() });
          return;
        }
        const id = trySend(c, { type: "chat.create", context: { follow_selection: true, pinned: [] } });
        if (id) {
          queued.current.set(id, text);
          dispatch({ type: "creating-chat", requestId: id });
        }
      },
      listTracks() {
        trySend(client(), { type: "tracks.list" });
      },
      updateSettings(change) {
        trySend(client(), { type: "settings.update", ...change });
      },
      testSettings() {
        trySend(client(), { type: "settings.test" });
      },
      useExample(name) {
        trySend(client(), { type: "live.use_example", name });
      },
      show(view) {
        dispatch({ type: "show", view });
      },
      dismissNotice() {
        dispatch({ type: "dismiss-notice" });
      },
      loadUsage(range) {
        const next = range ?? stateRef.current.usageRange;
        dispatch({ type: "usage-range", range: next });
        trySend(client(), { type: "usage.get", range: next });
      },
    };
  }, []);

  return [state, actions];
}

function trySend(client: HubClient | null, message: ClientEnvelope): string | null {
  if (!client?.connected) return null;
  try {
    return client.send(message);
  } catch {
    return null;
  }
}
