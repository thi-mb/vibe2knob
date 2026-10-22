import { useEffect, useRef, useState } from "react";
import type { ChatSummary, Usage, UsageReport } from "../protocol/types";
import type { UsageRange } from "../state/store";
import {
  KIND_LABELS,
  contextFill,
  fmtCost,
  fmtTokens,
  fmtUsage,
  modelName,
  partRows,
  promptTokens,
  totalTokens,
  unsplitTokens,
} from "../lib/usage";

const CONSOLE_URL = "https://platform.claude.com/usage";

/** Each part's tokens; estimated ones carry a "~". */
function PartList({ usage, restLabel }: { usage: Usage; restLabel: string }) {
  const rows = partRows(usage);
  const rest = unsplitTokens(usage);
  return (
    <dl className="usage-parts">
      {rows.map((r) => (
        <div key={r.key} className={`usage-part ${r.side}`}>
          <dt>{r.label}</dt>
          <dd className="mono">
            {r.exact ? "" : "~"}
            {fmtTokens(r.tokens)}
          </dd>
        </div>
      ))}
      {rest > 0 && (
        <div className="usage-part">
          <dt>{restLabel}</dt>
          <dd className="mono">{fmtTokens(rest)}</dd>
        </div>
      )}
    </dl>
  );
}

function CacheLine({ usage }: { usage: Usage }) {
  const prompt = promptTokens(usage);
  if (!prompt) return null;
  return (
    <div className="hint">
      Prompt {fmtTokens(prompt)}: {fmtTokens(usage.cache_read_tokens)} from cache, {fmtTokens(usage.cache_write_tokens)}{" "}
      written to cache, {fmtTokens(usage.input_tokens)} new. Output {fmtTokens(usage.output_tokens)}
      {usage.thinking_tokens ? `, ${fmtTokens(usage.thinking_tokens)} of it thinking` : ""}.
    </div>
  );
}

interface ChatUsageProps {
  chat: ChatSummary;
  onNewChat(): void;
}

/** The chat's total and context meter, under the context chips. Tap for the breakdown. */
export function ChatUsageBar({ chat, onNewChat }: ChatUsageProps) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const usage = chat.usage;
  const fill = contextFill(chat.context_tokens, chat.context_window);
  if (!usage && !fill) return null;
  const pct = fill?.share !== null && fill?.share !== undefined ? Math.max(1, Math.round(fill.share * 100)) : null;
  return (
    <div className="chat-usage" ref={box}>
      <div className="chat-usage-row">
        {fill && (
          <span
            className={`context-meter ${fill.long ? "long" : ""}`}
            role="meter"
            aria-label="Context used"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct ?? undefined}
            title={`The next message resends about ${fmtTokens(fill.tokens)} tokens${fill.window ? ` of a ${fmtTokens(fill.window)} window` : ""}.`}
          >
            <span className="context-meter-fill" style={{ width: `${pct ?? 0}%` }} />
          </span>
        )}
        {usage && (
          <button className="usage-total mono" aria-expanded={open} onClick={() => setOpen(!open)}>
            {fmtUsage(usage)}
          </button>
        )}
      </div>
      {fill?.long && (
        <div className="long-chat" role="status">
          <span>
            Long chat: each new message resends about {fmtTokens(fill.tokens)} tokens. A new chat is faster and cheaper.
          </span>
          <button className="link-button" onClick={onNewChat}>
            New chat
          </button>
        </div>
      )}
      {open && usage && (
        <div className="usage-pop" role="dialog" aria-label="This chat's usage">
          <div className="label">This chat</div>
          <PartList usage={usage} restLabel="Naming the chat" />
          <CacheLine usage={usage} />
          {fill && (
            <div className="hint">
              Next message resends ~{fmtTokens(fill.tokens)} tokens
              {pct !== null ? ` (${pct}% of ${modelLabel(usage)}'s window)` : ""}.
            </div>
          )}
          <div className="hint">Costs are estimates. Parts marked ~ are estimated by text length.</div>
        </div>
      )}
    </div>
  );
}

function modelLabel(u: Usage): string {
  return u.model ? modelName(u.model) : "the model";
}

/** The small line under an answer, shown on hover or focus. */
export function TurnUsage({ usage }: { usage: Usage }) {
  const cost = fmtCost(usage);
  const parts = partRows(usage)
    .map((r) => `${r.label} ${r.exact ? "" : "~"}${fmtTokens(r.tokens)}`)
    .join(" · ");
  return (
    <div className="turn-usage mono" tabIndex={0} title={parts || undefined}>
      {fmtTokens(promptTokens(usage))} in · {fmtTokens(usage.output_tokens)} out
      {cost ? ` · ${cost}` : ""}
      {usage.model ? ` · ${modelName(usage.model)}` : ""}
    </div>
  );
}

const RANGES: { id: UsageRange; label: string }[] = [
  { id: "today", label: "Today" },
  { id: "7d", label: "7 days" },
  { id: "30d", label: "30 days" },
  { id: "all", label: "All" },
];

interface UsageSectionProps {
  report: UsageReport | null;
  range: UsageRange;
  provider: string;
  onRange(range: UsageRange): void;
  onOpenChat(chatId: string): void;
}

/** Settings › Usage: totals, a daily chart, and where the tokens went. */
export function UsageSection({ report, range, provider, onRange, onOpenChat }: UsageSectionProps) {
  const t = report?.totals[range];
  return (
    <section aria-labelledby="s-usage">
      <div className="label" id="s-usage">
        Usage
      </div>
      <div className="segmented" role="group" aria-label="Usage range">
        {RANGES.map((r) => (
          <button key={r.id} type="button" aria-pressed={range === r.id} onClick={() => onRange(r.id)}>
            {r.label}
          </button>
        ))}
      </div>
      {!report || !t ? (
        <div className="hint">Loading…</div>
      ) : totalTokens(t) === 0 ? (
        <div className="hint">Nothing used {range === "today" ? "today" : "in this range"} yet.</div>
      ) : (
        <>
          <div className="usage-stats">
            <div className="stat">
              <div className="stat-value mono">{fmtTokens(totalTokens(t))}</div>
              <div className="hint">tokens</div>
            </div>
            <div className="stat">
              <div className="stat-value mono">{fmtCost(t) ?? "–"}</div>
              <div className="hint">{t.cost_usd === null ? "no price known" : "estimated"}</div>
            </div>
            <div className="stat">
              <div className="stat-value mono">
                {report.cache_hit_rate === null ? "–" : `${Math.round(report.cache_hit_rate * 100)}%`}
              </div>
              <div className="hint">prompt from cache</div>
            </div>
          </div>
          {report.days.length > 1 && <DailyChart report={report} />}
          <div className="label">Where the tokens went</div>
          <PartList usage={t} restLabel="Not split (titles, asks, older answers)" />
          <UsageTable title="By model" rows={report.by_model.map((g) => ({ key: g.key, name: modelName(g.key), usage: g.usage }))} />
          <UsageTable title="By kind" rows={report.by_kind.map((g) => ({ key: g.key, name: KIND_LABELS[g.key] ?? g.key, usage: g.usage }))} />
          {report.chats.length > 0 && (
            <UsageTable
              title="Chats"
              rows={report.chats.slice(0, 8).map((c) => ({
                key: c.chat_id,
                name: c.title ?? "Untitled chat",
                usage: c.usage,
                note: c.open ? undefined : "closed",
                onClick: c.open ? () => onOpenChat(c.chat_id) : undefined,
              }))}
            />
          )}
        </>
      )}
      <div className="hint">
        {provider === "ollama"
          ? "Ollama runs on your Mac, so it costs nothing. "
          : ""}
        Costs are estimates from prices checked on {report?.prices_checked_on ?? "…"}. Your real bill is in the{" "}
        <a href={CONSOLE_URL} target="_blank" rel="noreferrer">
          Anthropic Console
        </a>
        . Usage is counted on this Mac and never sent anywhere.
      </div>
    </section>
  );
}

interface Row {
  key: string;
  name: string;
  usage: Usage;
  note?: string;
  onClick?: () => void;
}

function UsageTable({ title, rows }: { title: string; rows: Row[] }) {
  if (!rows.length) return null;
  return (
    <table className="usage-table">
      <caption className="label">{title}</caption>
      <thead className="sr-only">
        <tr>
          <th scope="col">Name</th>
          <th scope="col">Tokens</th>
          <th scope="col">Cost</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            <th scope="row">
              {r.onClick ? (
                <button className="link-button" onClick={r.onClick}>
                  {r.name}
                </button>
              ) : (
                r.name
              )}
              {r.note && <span className="row-kind">{r.note}</span>}
            </th>
            <td className="mono">{fmtTokens(totalTokens(r.usage))}</td>
            <td className="mono">{fmtCost(r.usage) ?? "–"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Tokens per day, prompt and output stacked. */
function DailyChart({ report }: { report: UsageReport }) {
  const W = 320;
  const H = 96;
  const days = report.days;
  const max = Math.max(1, ...days.map((d) => totalTokens(d.usage)));
  const step = W / days.length;
  const bar = Math.max(2, Math.min(18, step * 0.7));
  const busiest = days.reduce((a, b) => (totalTokens(b.usage) > totalTokens(a.usage) ? b : a));
  const label = (iso: string) => {
    const d = new Date(`${iso}T12:00:00`);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };
  return (
    <figure className="usage-chart">
      <svg
        viewBox={`0 0 ${W} ${H + 16}`}
        role="img"
        aria-label={`Tokens per day. The busiest day was ${label(busiest.date)} with ${fmtTokens(totalTokens(busiest.usage))} tokens.`}
      >
        <line x1={0} x2={W} y1={H} y2={H} className="axis" />
        {days.map((d, i) => {
          const prompt = (promptTokens(d.usage) / max) * H;
          const out = (d.usage.output_tokens / max) * H;
          const x = i * step + (step - bar) / 2;
          return (
            <g key={d.date}>
              <title>{`${label(d.date)}: ${fmtUsage(d.usage)}`}</title>
              <rect x={x} y={H - prompt} width={bar} height={prompt} className="bar-prompt" rx={1} />
              <rect x={x} y={H - prompt - out} width={bar} height={out} className="bar-output" rx={1} />
            </g>
          );
        })}
        <text x={0} y={H + 13} className="tick">
          {label(days[0].date)}
        </text>
        <text x={W} y={H + 13} className="tick" textAnchor="end">
          {label(days[days.length - 1].date)}
        </text>
      </svg>
      <figcaption className="legend">
        <span>
          <span className="key-swatch bar-prompt" /> Prompt
        </span>
        <span>
          <span className="key-swatch bar-output" /> Output
        </span>
        <span className="hint">peak {fmtTokens(max)}/day</span>
      </figcaption>
    </figure>
  );
}
