import { describe, expect, it } from "vitest";
import type { ChatSummary, HubEnvelope, Usage, UsageReport } from "../protocol/types";
import { initialState, isStreaming, reducer, type Action, type PanelState } from "./store";

const chat = (id: string, updated_at = "2026-10-21T10:00:00Z"): ChatSummary => ({
  id,
  title: null,
  context: { follow_selection: true, pinned: [] },
  updated_at,
  usage: null,
  title_usage: null,
  context_tokens: null,
  context_window: null,
});

const usage = (input_tokens: number, output_tokens: number): Usage => ({
  input_tokens, output_tokens, cache_read_tokens: 0, cache_write_tokens: 0, thinking_tokens: 0, calls: 1,
  parts: {}, model: "claude-haiku-5-5", cost_usd: 0.001, cost_complete: true, prompt_cost_usd: 0.0008, output_cost_usd: 0.0002,
});

const hub = (message: HubEnvelope): Action => ({ type: "hub", message });

function welcomed(chats = [chat("a"), chat("b", "2026-10-21T11:00:00Z")]): PanelState {
  return reducer(
    initialState,
    hub({
      v: 1, type: "welcome", hub_version: "0.1", chats, examples: [],
      live: { connected: true, selected_track: null, detail: null, example: null },
      settings: { provider: "claude", model: null, api_key_set: true, monthly_budget_usd: null },
    }),
  );
}

describe("reducer", () => {
  it("opens the most recently updated chat on welcome", () => {
    expect(welcomed().activeChatId).toBe("b");
  });

  it("streams deltas into one assistant turn, then steps, then done", () => {
    let s = welcomed();
    s = reducer(s, { type: "user-sent", chatId: "b", requestId: "r1", text: "wider", at: "t" });
    s = reducer(s, hub({ v: 1, type: "chat.delta", chat_id: "b", turn_id: "t1", text: "Spread " }));
    s = reducer(s, hub({ v: 1, type: "chat.delta", chat_id: "b", turn_id: "t1", text: "it." }));
    expect(s.turns.b.map((t) => [t.role, t.text, t.pending])).toEqual([
      ["user", "wider", false],
      ["assistant", "Spread it.", true],
    ]);
    s = reducer(s, hub({ v: 1, type: "chat.steps", chat_id: "b", turn_id: "t1", steps: [], listen_for: "A/B it", caution: null, warnings: [] }));
    s = reducer(s, hub({ v: 1, type: "chat.done", chat_id: "b", turn_id: "t1", usage: usage(900, 40) }));
    expect(s.turns.b[1]).toMatchObject({ listen_for: "A/B it", pending: false });
    expect(s.turns.b[1].usage?.input_tokens).toBe(900);
  });

  it("tracks a turn's progress from the moment it's sent until it's done", () => {
    let s = welcomed();
    s = reducer(s, { type: "user-sent", chatId: "b", requestId: "r1", text: "wider", at: "2026-10-22T10:00:00Z" });
    expect(s.progress.b).toEqual({ since: "2026-10-22T10:00:00Z", phase: "reading", requestId: "r1" });
    expect(isStreaming(s, "b")).toBe(true);
    s = reducer(s, hub({ v: 1, type: "chat.status", chat_id: "b", turn_id: "t1", phase: "thinking", detail: null }));
    expect(s.progress.b).toMatchObject({ since: "2026-10-22T10:00:00Z", phase: "thinking" });
    expect(s.turns.b.at(-1)).toMatchObject({ id: "t1", pending: true, text: "" });
    s = reducer(s, hub({ v: 1, type: "chat.delta", chat_id: "b", turn_id: "t1", text: "Spread it." }));
    expect(s.progress.b.phase).toBe("writing");
    s = reducer(s, hub({ v: 1, type: "chat.status", chat_id: "b", turn_id: "t1", phase: "steps", detail: null }));
    expect(s.progress.b.phase).toBe("steps");
    s = reducer(s, hub({ v: 1, type: "chat.steps", chat_id: "b", turn_id: "t1", steps: [], listen_for: null, caution: null, warnings: [] }));
    expect(s.progress.b.phase).toBe("writing");
    s = reducer(s, hub({ v: 1, type: "chat.done", chat_id: "b", turn_id: "t1", usage: null }));
    expect(s.progress.b).toBeUndefined();
    expect(isStreaming(s, "b")).toBe(false);
  });

  it("stops waiting when the hub refuses the send", () => {
    let s = welcomed();
    s = reducer(s, { type: "user-sent", chatId: "b", requestId: "r1", text: "wider", at: "t" });
    s = reducer(s, hub({ v: 1, type: "error", request_id: "r1", code: "provider", message: "No API key.", chat_id: null, turn_id: null }));
    expect(s.progress.b).toBeUndefined();
    expect(s.notice).toBe("No API key.");
  });

  it("shows a failed turn's error in its chat, and other errors as a notice", () => {
    let s = welcomed();
    s = reducer(s, hub({ v: 1, type: "error", request_id: null, code: "provider", message: "Overloaded", chat_id: "b", turn_id: "t1" }));
    expect(s.turns.b[0].error).toBe("Overloaded");
    s = reducer(s, hub({ v: 1, type: "error", request_id: "r", code: "internal", message: "Oops", chat_id: null, turn_id: null }));
    expect(s.notice).toBe("Oops");
  });

  it("history replaces the local echo but keeps a turn still streaming", () => {
    let s = welcomed();
    s = reducer(s, { type: "user-sent", chatId: "b", requestId: "r1", text: "wider", at: "t" });
    s = reducer(s, hub({ v: 1, type: "chat.delta", chat_id: "b", turn_id: "t1", text: "Spr" }));
    s = reducer(s, hub({
      v: 1, type: "chat.history", request_id: null, chat_id: "b",
      turns: [{ id: "u1", role: "user", text: "wider", created_at: "t", steps: null, listen_for: null, caution: null, warnings: [], error: null, usage: null, looked_at: [] }],
    }));
    expect(s.turns.b.map((t) => t.id)).toEqual(["u1", "t1"]);
  });

  it("switches to a chat it created, and picks a neighbour when the active one closes", () => {
    let s = welcomed();
    s = reducer(s, { type: "creating-chat", requestId: "r9" });
    s = reducer(s, hub({ v: 1, type: "chat.changed", request_id: "r9", chat: chat("c") }));
    expect(s.activeChatId).toBe("c");
    expect(s.turns.c).toEqual([]);
    s = reducer(s, hub({ v: 1, type: "chat.closed", request_id: null, chat_id: "c" }));
    expect(s.activeChatId).toBe("b");
  });

  it("forgets loaded turns on a new welcome, so each chat reloads after a reconnect", () => {
    let s = welcomed();
    s = reducer(s, hub({ v: 1, type: "chat.delta", chat_id: "b", turn_id: "t1", text: "x" }));
    s = reducer(s, { type: "connection", connection: "connecting" });
    expect(s.ready).toBe(false);
    s = reducer(s, hub({ v: 1, type: "welcome", hub_version: "0.1", chats: [chat("b")], examples: [], live: s.live, settings: s.settings }));
    expect(s.turns).toEqual({});
    expect(s.activeChatId).toBe("b");
  });

  it("keeps the usage report only for the range asked for last", () => {
    const report = (range: UsageReport["range"]): UsageReport => ({
      v: 1, type: "usage.report", request_id: null, range, totals: {}, days: [], by_model: [], by_kind: [],
      chats: [], cache_hit_rate: null, prices_checked_on: "2026-10-22",
    });
    let s = reducer(welcomed(), { type: "usage-range", range: "7d" });
    s = reducer(s, hub(report("30d")));
    expect(s.usage).toBeNull();
    s = reducer(s, hub(report("7d")));
    expect(s.usage?.range).toBe("7d");
  });
});
