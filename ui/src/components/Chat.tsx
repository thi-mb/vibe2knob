import { useEffect, useRef, useState } from "react";
import { parseTarget } from "../lib/knob";
import { type Inline, parseMarkdown } from "../lib/markdown";
import { trackColor } from "../lib/live";
import { load, save } from "../lib/storage";
import type { Step } from "../protocol/types";
import type { Turn } from "../state/store";
import { Icon } from "./Icon";
import { Knob } from "./Knob";

function Inlines({ nodes }: { nodes: Inline[] }) {
  return nodes.map((n, i) => {
    if (typeof n === "string") return n;
    const Tag = n.type;
    return (
      <Tag key={i}>
        <Inlines nodes={n.children} />
      </Tag>
    );
  });
}

function Prose({ text, streaming }: { text: string; streaming: boolean }) {
  const blocks = parseMarkdown(text, streaming);
  const cursor = streaming && <span className="cursor" aria-hidden="true" />;
  return (
    <div className="prose">
      {blocks.map((b, i) => {
        const end = i === blocks.length - 1 && cursor;
        if (b.type === "p")
          return (
            <p key={i}>
              <Inlines nodes={b.children} />
              {end}
            </p>
          );
        const items = b.items.map((item, k) => (
          <li key={k}>
            <Inlines nodes={item} />
            {k === b.items.length - 1 && end}
          </li>
        ));
        return b.type === "ul" ? (
          <ul key={i}>{items}</ul>
        ) : (
          <ol key={i} start={b.start}>
            {items}
          </ol>
        );
      })}
    </div>
  );
}

function useDoneSteps(chatId: string, turnId: string): [Set<number>, (i: number) => void] {
  const key = `steps.${chatId}.${turnId}`;
  const [done, setDone] = useState(() => new Set(load<number[]>(key, [])));
  const toggle = (i: number) => {
    setDone((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      save(key, [...next]);
      return next;
    });
  };
  return [done, toggle];
}

function stepTitle(step: Step): string {
  return step.parameter ? `${step.device} › ${step.parameter}` : step.device;
}

interface StepCardProps {
  step: Step;
  number: number;
  done: boolean;
  current: boolean;
  showTrack: boolean;
  trackColorValue: number | null;
  onToggle(): void;
  onWhy(): void;
}

function StepCard({ step, number, done, current, showTrack, trackColorValue, onToggle, onWhy }: StepCardProps) {
  const target = parseTarget(step.value_range);
  const adding = step.action === "add_device";
  const classes = ["step", done && "done", current && !done && "current", adding && !current && "optional"];
  const verb = { add_device: "ADD", enable: "ON", disable: "OFF", remove: "REMOVE", reorder: "MOVE", adjust: null }[step.action];
  return (
    <li className={classes.filter(Boolean).join(" ")}>
      <button
        className="step-check"
        aria-pressed={done}
        aria-label={`Step ${number}: ${stepTitle(step)}. ${done ? "Done" : "Mark done"}`}
        onClick={onToggle}
      >
        <span className={`led ${done ? "ok" : current ? "on" : ""}`} />
      </button>
      <div className="step-body">
        {showTrack && step.track && (
          <div className="step-track">
            <span className="swatch" style={{ background: trackColor(trackColorValue) }} />
            {step.track}
          </div>
        )}
        <div className="step-head">
          <div className="step-title">
            {verb && <span className="badge">{verb}</span>}
            <strong>{step.device}</strong>
            {step.parameter && (
              <>
                {" "}
                <span className="sep">›</span> {step.parameter}
              </>
            )}
          </div>
          {target && (
            <span className="step-value">
              <Knob target={target} />
              <span>{target.label}</span>
            </span>
          )}
        </div>
        {!target && step.value_range && <div className="step-value-text">{step.value_range}</div>}
        <div className="step-explain">{step.explanation}</div>
        <button className="step-why" onClick={onWhy}>
          WHY? ›
        </button>
      </div>
    </li>
  );
}

interface AssistantTurnProps {
  chatId: string;
  turn: Turn;
  latest: boolean;
  trackColors: Map<string, number | null>;
  onAsk(text: string): void;
}

function AssistantTurn({ chatId, turn, latest, trackColors, onAsk }: AssistantTurnProps) {
  const [done, toggle] = useDoneSteps(chatId, turn.id);
  const steps = turn.steps ?? [];
  const current = latest ? steps.findIndex((_, i) => !done.has(i)) : -1;
  const tracks = new Set(steps.map((s) => s.track).filter(Boolean));
  return (
    <>
      {turn.text ? (
        <Prose text={turn.text} streaming={turn.pending} />
      ) : (
        turn.pending && (
          <div className="thinking" role="status">
            <span className="led small on blink" />
            READING THE SESSION
          </div>
        )
      )}
      {steps.length > 0 && (
        <ol className="steps" aria-label="Steps">
          {steps.map((step, i) => (
            <StepCard
              key={i}
              step={step}
              number={i + 1}
              done={done.has(i)}
              current={i === current}
              showTrack={tracks.size > 1}
              trackColorValue={step.track ? (trackColors.get(step.track) ?? null) : null}
              onToggle={() => toggle(i)}
              onWhy={() => onAsk(`Why does step ${i + 1} (${stepTitle(step)}) work?`)}
            />
          ))}
        </ol>
      )}
      {turn.listen_for && (
        <div className="listen">
          <Icon name="listen" size={18} />
          <div>
            <strong>Listen</strong>
            {turn.listen_for}
          </div>
        </div>
      )}
      {turn.caution && (
        <div className="caution">
          <strong>CAUTION</strong>
          {turn.caution}
        </div>
      )}
      {turn.warnings.map((w, i) => (
        <div className="warning" key={i}>
          <Icon name="alert" size={14} />
          <span>{w}</span>
        </div>
      ))}
      {turn.error && (
        <div className="turn-error" role="alert">
          {turn.error}
        </div>
      )}
    </>
  );
}

interface ConversationProps {
  chatId: string;
  turns: Turn[] | undefined;
  trackColors: Map<string, number | null>;
  onAsk(text: string): void;
  /** Shown after the turns, e.g. the offline card in a chat that already has answers. */
  after?: React.ReactNode;
  empty?: React.ReactNode;
}

export function Conversation({ chatId, turns, trackColors, onAsk, after, empty }: ConversationProps) {
  const end = useRef<HTMLDivElement>(null);
  const lastText = turns?.at(-1)?.text.length ?? 0;
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [turns?.length, lastText]);
  const lastAssistant = turns ? turns.findLastIndex((t) => t.role === "assistant") : -1;
  return (
    <div className="conversation" aria-live="polite" aria-busy={turns?.some((t) => t.pending) ?? false}>
      {turns === undefined ? (
        <div className="thinking">
          <span className="led small on blink" />
          LOADING
        </div>
      ) : turns.length === 0 ? (
        empty
      ) : (
        turns.map((turn, i) =>
          turn.role === "user" ? (
            <div className="user-msg" key={turn.id}>
              {turn.text}
            </div>
          ) : (
            <AssistantTurn
              key={turn.id}
              chatId={chatId}
              turn={turn}
              latest={i === lastAssistant}
              trackColors={trackColors}
              onAsk={onAsk}
            />
          ),
        )
      )}
      {after}
      <div ref={end} />
    </div>
  );
}

interface ComposerProps {
  disabled: boolean;
  busy: boolean;
  placeholder: string;
  quickReplies: string[];
  onSend(text: string): void;
  onAt(): void;
}

export function Composer({ disabled, busy, placeholder, quickReplies, onSend, onAt }: ComposerProps) {
  const [text, setText] = useState("");
  const submit = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed || disabled || busy) return;
    onSend(trimmed);
    setText("");
  };
  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        submit(text);
      }}
    >
      {quickReplies.length > 0 && !busy && !disabled && (
        <div className="quick">
          {quickReplies.map((q) => (
            <button type="button" key={q} onClick={() => submit(q)}>
              {q}
            </button>
          ))}
        </div>
      )}
      <div className="input-row">
        <label htmlFor="ask" className="sr-only">
          Describe the vibe
        </label>
        <input
          id="ask"
          className="field"
          autoComplete="off"
          placeholder={placeholder}
          value={text}
          disabled={disabled}
          onChange={(e) => {
            const value = e.target.value;
            if (value.endsWith("@") && value.length === text.length + 1) {
              setText(value.slice(0, -1));
              onAt();
              return;
            }
            setText(value);
          }}
        />
        <button className="send" type="submit" aria-label="Send" disabled={disabled || busy || !text.trim()}>
          <Icon name="send" strokeWidth={2.5} />
        </button>
      </div>
    </form>
  );
}
