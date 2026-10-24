import { useEffect, useRef, useState } from "react";
import { parseTarget } from "../lib/knob";
import { type Inline, parseMarkdown } from "../lib/markdown";
import { trackColor } from "../lib/live";
import { load, save } from "../lib/storage";
import type { Step, TrackRef } from "../protocol/types";
import type { Phase, Progress, Turn } from "../state/store";
import { Icon } from "./Icon";
import { Knob } from "./Knob";
import { TurnUsage } from "./Usage";

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

/** The tracks the model read for an answer. Each chip pins its track to the chat. */
function LookedAt({ tracks, inContext, onPin }: { tracks: TrackRef[]; inContext: Set<number>; onPin(track: TrackRef): void }) {
  return (
    <div className="looked" role="group" aria-label="Looked at">
      <span className="looked-label">LOOKED AT</span>
      {tracks.map((t) => {
        const already = inContext.has(t.index);
        return (
          <button
            key={t.index}
            className="chip looked-chip"
            disabled={already}
            aria-label={already ? `${t.name} is in this chat` : `Pin ${t.name} to this chat`}
            title={already ? "Already in this chat" : "Pin to this chat"}
            onClick={() => onPin(t)}
          >
            <span className="swatch" style={{ background: trackColor(t.color) }} />
            {t.name}
            {!already && <span className="looked-pin">+</span>}
          </button>
        );
      })}
    </div>
  );
}

interface AssistantTurnProps {
  chatId: string;
  turn: Turn;
  latest: boolean;
  /** False once the prose is done and the progress line has taken over. */
  writing: boolean;
  trackColors: Map<string, number | null>;
  inContext: Set<number>;
  onAsk(text: string): void;
  onPin(track: TrackRef): void;
}

function AssistantTurn({ chatId, turn, latest, writing, trackColors, inContext, onAsk, onPin }: AssistantTurnProps) {
  const [done, toggle] = useDoneSteps(chatId, turn.id);
  const steps = turn.steps ?? [];
  const current = latest ? steps.findIndex((_, i) => !done.has(i)) : -1;
  const tracks = new Set(steps.map((s) => s.track).filter(Boolean));
  return (
    <div className="assistant-turn">
      {turn.looked_at.length > 0 && <LookedAt tracks={turn.looked_at} inContext={inContext} onPin={onPin} />}
      {turn.text && <Prose text={turn.text} streaming={turn.pending && writing} />}
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
      {turn.usage && !turn.pending && <TurnUsage usage={turn.usage} />}
    </div>
  );
}

const PHASE_LABEL: Record<Exclude<Phase, "writing">, string> = {
  reading: "READING THE SESSION",
  thinking: "THINKING",
  steps: "WRITING STEPS",
};

/** "7s", then "1:05" past a minute. */
export function elapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** The line that shows a turn is working while nothing else does. */
function TurnProgress({ progress }: { progress: Progress }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  if (progress.phase === "writing") return null;
  const since = Date.parse(progress.since);
  return (
    <div className="thinking progress" role="status">
      <span className="led small on blink" />
      {progress.detail ? <span className="progress-detail">{progress.detail}</span> : PHASE_LABEL[progress.phase]}
      <span className="elapsed">· {elapsed(Number.isNaN(since) ? 0 : now - since)}</span>
    </div>
  );
}

interface ConversationProps {
  chatId: string;
  turns: Turn[] | undefined;
  trackColors: Map<string, number | null>;
  /** Track indexes already in the chat's context, so their looked-at chips don't offer a pin. */
  inContext: Set<number>;
  onAsk(text: string): void;
  onPin(track: TrackRef): void;
  /** The turn in flight, if any. */
  progress?: Progress;
  /** Shown after the turns, e.g. the offline card in a chat that already has answers. */
  after?: React.ReactNode;
  empty?: React.ReactNode;
}

export function Conversation({ chatId, turns, trackColors, inContext, onAsk, onPin, progress, after, empty }: ConversationProps) {
  const end = useRef<HTMLDivElement>(null);
  const lastText = turns?.at(-1)?.text.length ?? 0;
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [turns?.length, lastText, progress?.phase, progress?.detail]);
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
              writing={!progress || progress.phase === "writing"}
              trackColors={trackColors}
              inContext={inContext}
              onAsk={onAsk}
              onPin={onPin}
            />
          ),
        )
      )}
      {turns !== undefined && progress && <TurnProgress progress={progress} />}
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
