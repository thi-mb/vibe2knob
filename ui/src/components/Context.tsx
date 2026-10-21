import { useEffect, useRef } from "react";
import { deviceChain, trackColor } from "../lib/live";
import type { ChatSummary, ContextSet, TrackRef } from "../protocol/types";
import { Icon } from "./Icon";

interface TabsProps {
  chats: ChatSummary[];
  activeId: string | null;
  busyIds: Set<string>;
  onSelect(id: string): void;
  onClose(id: string): void;
  onNew(): void;
}

export function Tabs({ chats, activeId, busyIds, onSelect, onClose, onNew }: TabsProps) {
  return (
    <nav className="tabs" aria-label="Chats">
      {chats.map((chat) => {
        const active = chat.id === activeId;
        const title = chat.title ?? "New chat";
        return (
          <div key={chat.id} className={`key tab ${active ? "lit" : ""}`}>
            {(active || busyIds.has(chat.id)) && (
              <span className={`led small on ${busyIds.has(chat.id) ? "blink" : ""}`} />
            )}
            <button className="tab-title" aria-current={active ? "page" : undefined} onClick={() => onSelect(chat.id)}>
              {title}
            </button>
            <button className="tab-close" aria-label={`Close ${title}`} onClick={() => onClose(chat.id)}>
              ×
            </button>
          </div>
        );
      })}
      <button className="key" aria-label="New chat" onClick={onNew}>
        +
      </button>
    </nav>
  );
}

interface ContextBarProps {
  context: ContextSet;
  selected: TrackRef | null;
  tracks: TrackRef[];
  canSee: boolean;
  example: string | null;
  pickerOpen: boolean;
  onChange(context: ContextSet): void;
  onPicker(open: boolean): void;
}

export function ContextBar({ context, selected, tracks, canSee, example, pickerOpen, onChange, onPicker }: ContextBarProps) {
  const byIndex = new Map(tracks.map((t) => [t.index, t]));
  const pinned = context.pinned.map((i) => byIndex.get(i)).filter((t): t is TrackRef => !!t);
  const inContext = new Set(context.pinned);
  if (context.follow_selection && selected) inContext.add(selected.index);
  const inSummary = tracks.filter((t) => !inContext.has(t.index)).length;
  const general = !context.follow_selection && pinned.length === 0;

  const toggleFollow = () => onChange({ ...context, follow_selection: !context.follow_selection });
  const unpin = (index: number) => onChange({ ...context, pinned: context.pinned.filter((i) => i !== index) });

  let line: string;
  if (general) line = "Ask anything about Live. Add a track with @ to talk about it.";
  else if (!canSee) line = "Live isn't connected, so I can't read these tracks yet.";
  else {
    const parts: string[] = [];
    if (context.follow_selection) {
      parts.push(pinned.length ? `Following ${selected?.name ?? "your selection"}` : "Following your selection");
    }
    if (pinned.length) parts.push(`${pinned.length} pinned`);
    if (context.follow_selection && !pinned.length) parts.push(selected ? deviceChain(selected) : "No track selected");
    else if (inSummary) parts.push(`+${inSummary} in summary`);
    line = parts.join(" · ");
  }

  return (
    <div className="context">
      <div className="chips">
        {general ? (
          <button className="chip off" onClick={toggleFollow} aria-label="General chat with no tracks. Follow your selection in Live">
            General · no tracks
          </button>
        ) : context.follow_selection ? (
          <button
            className="chip"
            onClick={toggleFollow}
            aria-pressed="true"
            aria-label={`${selected?.name ?? "No track selected"}, following your selection in Live. Stop following`}
          >
            <span style={{ color: "var(--accent)", display: "inline-flex" }}>
              <Icon name="follow" size={12} strokeWidth={2.5} />
            </span>
            {selected && <span className="swatch" style={{ background: trackColor(selected.color) }} />}
            {selected?.name ?? (example ? example : "No track selected")}
          </button>
        ) : (
          <button className="chip off" onClick={toggleFollow} aria-pressed="false" aria-label="Follow your selection in Live">
            <Icon name="follow" size={12} />
            Follow selection
          </button>
        )}
        {pinned.map((t) => (
          <span className="chip" key={t.index}>
            <span className="swatch" style={{ background: trackColor(t.color) }} />
            {t.name}
            <button className="chip-remove" aria-label={`Remove ${t.name}`} onClick={() => unpin(t.index)}>
              ×
            </button>
          </span>
        ))}
        <button className="chip add" aria-expanded={pickerOpen} aria-haspopup="listbox" onClick={() => onPicker(!pickerOpen)}>
          {pinned.length || !context.follow_selection ? "@" : "@ Add track"}
        </button>
        {canSee && !general && pinned.length === 0 && inSummary > 0 && (
          <span className="summary-count">+{inSummary} in summary</span>
        )}
      </div>
      <div className="context-line">{line}</div>
      {pickerOpen && (
        <TrackPicker
          tracks={tracks}
          selected={context.follow_selection ? selected : null}
          pinned={new Set(context.pinned)}
          onToggle={(index) =>
            onChange({
              ...context,
              pinned: context.pinned.includes(index) ? context.pinned.filter((i) => i !== index) : [...context.pinned, index],
            })
          }
          onClose={() => onPicker(false)}
        />
      )}
    </div>
  );
}

interface TrackPickerProps {
  tracks: TrackRef[];
  selected: TrackRef | null;
  pinned: Set<number>;
  onToggle(index: number): void;
  onClose(): void;
}

function TrackPicker({ tracks, selected, pinned, onToggle, onClose }: TrackPickerProps) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const onDown = (e: MouseEvent) => {
      const target = e.target as Element | null;
      if (box.current && target && !box.current.contains(target) && !target.closest?.(".chip.add")) onClose();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [onClose]);

  return (
    <div
      className="picker"
      ref={box}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          const rows = [...(box.current?.querySelectorAll<HTMLButtonElement>(".picker-row") ?? [])];
          const i = rows.indexOf(document.activeElement as HTMLButtonElement);
          rows[(i + (e.key === "ArrowDown" ? 1 : rows.length - 1)) % rows.length]?.focus();
        }
      }}
    >
      <div className="picker-head">ADD TO THIS CHAT</div>
      {tracks.length === 0 && <div className="hint" style={{ padding: 8 }}>No tracks to add. Is Live connected?</div>}
      <div role="listbox" aria-label="Tracks" aria-multiselectable="true">
        {tracks.map((t) => {
          const following = selected?.index === t.index;
          const on = pinned.has(t.index);
          return (
            <button
              key={t.index}
              className="picker-row"
              role="option"
              aria-selected={on}
              disabled={following && !on}
              onClick={() => onToggle(t.index)}
            >
              <span className="pin-box">{(on || following) && <Icon name="check" size={12} strokeWidth={3} />}</span>
              <span className="swatch" style={{ background: trackColor(t.color) }} />
              <span style={{ minWidth: 0 }}>
                <span className="row-name">{t.name}</span>
                {t.type === "group" && <span className="row-kind">group</span>}
                {following && <span className="row-kind">following</span>}
                <div className="row-detail">{deviceChain(t)}</div>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
