// Formatting token usage for the chat header, the answer footer and Settings › Usage.
// Counts come from the hub; nothing here estimates anything new.

import type { Usage } from "../protocol/types";

/** The parts a prompt and an answer are split into, in display order. */
export const PARTS = [
  { key: "instructions", label: "Instructions", exact: true, side: "in" },
  { key: "tools", label: "Tool definitions", exact: true, side: "in" },
  { key: "live", label: "Live context", exact: false, side: "in" },
  { key: "history", label: "Chat history", exact: false, side: "in" },
  { key: "message", label: "Your message", exact: false, side: "in" },
  { key: "thinking", label: "Thinking", exact: true, side: "out" },
  { key: "answer", label: "Answer", exact: false, side: "out" },
  { key: "steps", label: "Steps", exact: false, side: "out" },
] as const;

export const KIND_LABELS: Record<string, string> = { chat: "Chats", title: "Chat titles", ask: "One-shot ask" };

/** Past this many resent tokens, or this share of the window, the header suggests a new chat. */
export const LONG_CHAT_TOKENS = 150_000;
export const LONG_CHAT_SHARE = 0.7;

export function promptTokens(u: Usage): number {
  return u.input_tokens + u.cache_read_tokens + u.cache_write_tokens;
}

export function totalTokens(u: Usage): number {
  return promptTokens(u) + u.output_tokens;
}

/** 950, 18.2k, 1.2M. */
export function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${trim(n / 1000)}k`;
  return `${trim(n / 1_000_000)}M`;
}

function trim(x: number): string {
  return x >= 100 ? String(Math.round(x)) : x.toFixed(1).replace(/\.0$/, "");
}

/** "~$0.04", "<$0.01", "$0" for free local models; null when no price is known. */
export function fmtCost(u: Usage): string | null {
  return fmtDollars(u.cost_usd, u.cost_complete);
}

/** A dollar estimate; `complete` false adds "+", since some calls had no known price. */
export function fmtDollars(usd: number | null, complete = true): string | null {
  if (usd === null) return null;
  const more = complete ? "" : "+";
  if (usd === 0) return `$0${more}`;
  if (usd < 0.01) return `<$0.01${more}`;
  const digits = usd >= 100 ? 0 : 2;
  return `~$${usd.toFixed(digits)}${more}`;
}

/** `a` without `b`, e.g. a chat's usage without naming it. */
export function minus(a: Usage, b: Usage | null): Usage {
  if (!b) return a;
  const sub = (x: number | null, y: number | null) => (x === null ? null : Math.max(0, x - (y ?? 0)));
  const parts: Record<string, number> = {};
  for (const [k, v] of Object.entries(a.parts)) parts[k] = Math.max(0, v - (b.parts[k] ?? 0));
  return {
    ...a,
    input_tokens: Math.max(0, a.input_tokens - b.input_tokens),
    output_tokens: Math.max(0, a.output_tokens - b.output_tokens),
    cache_read_tokens: Math.max(0, a.cache_read_tokens - b.cache_read_tokens),
    cache_write_tokens: Math.max(0, a.cache_write_tokens - b.cache_write_tokens),
    thinking_tokens: Math.max(0, a.thinking_tokens - b.thinking_tokens),
    parts,
    cost_usd: sub(a.cost_usd, b.cost_usd),
    prompt_cost_usd: sub(a.prompt_cost_usd, b.prompt_cost_usd),
    output_cost_usd: sub(a.output_cost_usd, b.output_cost_usd),
  };
}

/** "18.2k tokens · ~$0.04", or just the tokens when there's no price. */
export function fmtUsage(u: Usage): string {
  const cost = fmtCost(u);
  return `${fmtTokens(totalTokens(u))} tokens${cost ? ` · ${cost}` : ""}`;
}

/** The parts that have tokens, with the share of the call each one is. */
export function partRows(u: Usage): { key: string; label: string; exact: boolean; side: "in" | "out"; tokens: number }[] {
  return PARTS.filter((p) => (u.parts[p.key] ?? 0) > 0).map((p) => ({ ...p, tokens: u.parts[p.key] }));
}

/** Tokens no part accounts for: answers saved before usage was split. */
export function unsplitTokens(u: Usage): number {
  const split = Object.values(u.parts).reduce((a, b) => a + b, 0);
  return Math.max(0, totalTokens(u) - split);
}

export interface ContextFill {
  tokens: number;
  window: number | null;
  /** 0 to 1, or null with no known window. */
  share: number | null;
  long: boolean;
}

export function contextFill(tokens: number | null, window: number | null): ContextFill | null {
  if (!tokens) return null;
  const share = window ? Math.min(1, tokens / window) : null;
  return { tokens, window, share, long: tokens >= LONG_CHAT_TOKENS || (share ?? 0) >= LONG_CHAT_SHARE };
}

/** "claude-haiku-5-5" → "Haiku 5.5". Anything else is shown as it is. */
export function modelName(id: string): string {
  const m = /^claude-([a-z]+)-(\d+)(?:-(\d))?(?:-\d{8})?$/.exec(id);
  if (!m) return id;
  return `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}${m[3] ? `.${m[3]}` : ""}`;
}
