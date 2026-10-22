import { useState } from "react";
import type { LiveStatus, Settings, TrackRef, UsageReport } from "../protocol/types";
import type { UsageRange } from "../state/store";
import { modelName } from "../lib/usage";
import { Icon } from "./Icon";
import { UsageSection } from "./Usage";

export const CLAUDE_MODELS = [
  { id: "claude-haiku-5-5", label: "Haiku 5.5 · fast and cheap (recommended)" },
  { id: "claude-sonnet-5-5", label: "Sonnet 5.5 · slower, more detail" },
];
const DEFAULT_OLLAMA_MODEL = "llama3.1";

export type Theme = "system" | "dark" | "light";

interface ModelFormProps {
  settings: Settings;
  testResult: string | null;
  compact?: boolean;
  onSave(change: { provider?: "claude" | "ollama"; model?: string; api_key?: string }): void;
  onTest(): void;
}

/** Provider, key and model. Shared by the setup card and the settings page. */
function ModelForm({ settings, testResult, compact, onSave, onTest }: ModelFormProps) {
  const [provider, setProvider] = useState(settings.provider);
  const [key, setKey] = useState("");
  const [ollamaModel, setOllamaModel] = useState(
    settings.provider === "ollama" ? (settings.model ?? DEFAULT_OLLAMA_MODEL) : DEFAULT_OLLAMA_MODEL,
  );
  const ok = testResult !== null && /works|ok|connected/i.test(testResult);

  const connect = () => {
    if (provider === "claude") {
      onSave({ provider, ...(key.trim() ? { api_key: key.trim() } : {}) });
      setKey("");
    } else {
      onSave({ provider, model: ollamaModel.trim() || DEFAULT_OLLAMA_MODEL });
    }
    onTest();
  };

  return (
    <>
      <div className="segmented" role="group" aria-label="Model provider">
        <button type="button" aria-pressed={provider === "claude"} onClick={() => setProvider("claude")}>
          Claude
        </button>
        <button type="button" aria-pressed={provider === "ollama"} onClick={() => setProvider("ollama")}>
          {compact ? "Ollama (free, local)" : "Ollama (local)"}
        </button>
      </div>
      {provider === "claude" ? (
        <div className="form-row">
          <label htmlFor="api-key">
            <span>Anthropic API key</span>
            <a href="https://console.anthropic.com" target="_blank" rel="noreferrer">
              Get a key
            </a>
          </label>
          <div className="input-row">
            <input
              id="api-key"
              className="field mono"
              type="password"
              autoComplete="off"
              placeholder={settings.api_key_set ? "Saved · paste a new one to replace it" : "sk-ant-…"}
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
            {!compact && (
              <button className="secondary" type="button" onClick={connect}>
                {key.trim() ? "Save" : "Test"}
              </button>
            )}
          </div>
          <div className="hint">Saved in your Mac's Keychain and only sent to Anthropic. You pay for your own usage.</div>
        </div>
      ) : (
        <div className="form-row">
          <label htmlFor="ollama-model">
            <span>Ollama model</span>
            <a href="https://ollama.com" target="_blank" rel="noreferrer">
              Get Ollama
            </a>
          </label>
          <div className="input-row">
            <input
              id="ollama-model"
              className="field mono"
              autoComplete="off"
              value={ollamaModel}
              onChange={(e) => setOllamaModel(e.target.value)}
            />
            {!compact && (
              <button className="secondary" type="button" onClick={connect}>
                Test
              </button>
            )}
          </div>
          <div className="hint">Runs on your Mac, free. Answers are slower and less precise than Claude's.</div>
        </div>
      )}
      {testResult !== null &&
        (ok ? (
          <div className="result-ok" role="status">
            <Icon name="check" size={14} strokeWidth={3} />
            {testResult}
          </div>
        ) : (
          <div className="result-bad" role="alert">
            {testResult}
          </div>
        ))}
      {compact && (
        <button
          className="primary"
          type="button"
          disabled={provider === "claude" && !key.trim() && !settings.api_key_set}
          onClick={connect}
        >
          Connect
        </button>
      )}
      {!compact && provider === "claude" && (
        <div className="form-row">
          <label htmlFor="model">Model</label>
          <select
            id="model"
            className="field"
            value={settings.provider === "claude" ? (settings.model ?? CLAUDE_MODELS[0].id) : CLAUDE_MODELS[0].id}
            onChange={(e) => onSave({ provider: "claude", model: e.target.value })}
          >
            {claudeModels(settings).map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
      )}
    </>
  );
}

/** The listed models, plus the one in use if it isn't listed (e.g. from $VIBE2KNOB_MODEL). */
function claudeModels(settings: Settings) {
  const current = settings.provider === "claude" ? settings.model : null;
  if (!current || CLAUDE_MODELS.some((m) => m.id === current)) return CLAUDE_MODELS;
  return [...CLAUDE_MODELS, { id: current, label: `${modelName(current)} · in use` }];
}

export function modelReady(settings: Settings, testResult: string | null): boolean {
  if (settings.provider === "claude") return settings.api_key_set;
  return testResult !== null && /works|ok|connected/i.test(testResult);
}

interface SetupProps {
  settings: Settings;
  testResult: string | null;
  live: LiveStatus;
  examples: TrackRef[];
  onSave: ModelFormProps["onSave"];
  onTest(): void;
  onExample(name: string): void;
  onFinish(): void;
}

/** The three-step first-run card: model (required), Live (skippable), try a vibe. */
export function SetupCard({ settings, testResult, live, examples, onSave, onTest, onExample, onFinish }: SetupProps) {
  const [skippedLive, setSkippedLive] = useState(false);
  const modelOk = modelReady(settings, testResult);
  const liveOk = live.connected || live.example !== null;
  const step = !modelOk ? 1 : !liveOk && !skippedLive ? 2 : 3;
  const cls = (n: number) => `setup-step ${step === n ? "active" : step > n ? "done" : ""}`;

  return (
    <div className="settings">
      <div style={{ display: "flex", flexDirection: "column", gap: 6, paddingTop: 8 }}>
        <h1 style={{ margin: 0, fontSize: 20, lineHeight: 1.25 }}>Tell it the vibe. You turn the knobs.</h1>
        <div className="quiet" style={{ color: "var(--text-quiet)" }}>
          vibe2knob reads your Live set and shows you which stock devices and knobs get you there, and why. Three quick
          steps to start.
        </div>
      </div>
      <div className="card">
        <div className={cls(1)}>
          <span className="num">{step > 1 ? <Icon name="check" size={12} strokeWidth={3} /> : 1}</span>
          <div className="body">
            <div className="title">Connect a model</div>
            {step === 1 && (
              <ModelForm settings={settings} testResult={testResult} compact onSave={onSave} onTest={onTest} />
            )}
          </div>
        </div>
        <div className={cls(2)}>
          <span className="num">{step > 2 ? <Icon name="check" size={12} strokeWidth={3} /> : 2}</span>
          <div className="body">
            <div className="title">
              Connect Live <span className="label">optional</span>
            </div>
            {step === 2 && (
              <>
                <LiveChecks live={live} />
                <button className="secondary" type="button" onClick={() => setSkippedLive(true)}>
                  Skip, try an example
                </button>
              </>
            )}
            {step !== 2 && (
              <div className="hint">
                {live.connected
                  ? `Connected${live.selected_track ? ` · ${live.selected_track.name} selected` : ""}`
                  : "I'll check for AbletonOSC. Skip it to try an example track."}
              </div>
            )}
          </div>
        </div>
        <div className={cls(3)}>
          <span className="num">3</span>
          <div className="body">
            <div className="title">Try a vibe</div>
            <div className="hint">
              {liveOk
                ? "Select a track in Live and say how it should feel: wider, warmer, punchier."
                : "Pick an example track, then say how it should feel: wider, warmer, punchier."}
            </div>
            {step === 3 && !liveOk && (
              <div className="examples">
                {examples.map((t) => (
                  <button
                    key={t.name}
                    className="key"
                    onClick={() => {
                      onExample(t.name);
                      onFinish();
                    }}
                  >
                    {t.name}
                  </button>
                ))}
              </div>
            )}
            {step === 3 && liveOk && (
              <button className="primary" type="button" onClick={onFinish}>
                Start
              </button>
            )}
          </div>
        </div>
      </div>
      <div className="settings-foot">
        <span>Open source</span>
        <a href="https://github.com/thi-mb/vibe2knob" target="_blank" rel="noreferrer">
          github.com/thi-mb/vibe2knob
        </a>
      </div>
    </div>
  );
}

function LiveChecks({ live }: { live: LiveStatus }) {
  if (live.connected) {
    return (
      <div className="check-item">
        <span className="led ok" />
        <div>Connected through AbletonOSC</div>
      </div>
    );
  }
  return (
    <div className="check-list">
      <div className="check-item">
        <span className="led bad" />
        <div>
          <div>{live.detail ?? "Live isn't answering"}</div>
          <div className="hint">
            Open Live, then in Preferences › Link, Tempo &amp; MIDI pick <strong>AbletonOSC</strong> as a Control
            Surface.{" "}
            <a href="https://github.com/ideoforms/AbletonOSC" target="_blank" rel="noreferrer">
              How to install AbletonOSC
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

interface OfflineProps {
  live: LiveStatus;
  examples: TrackRef[];
  onCheck(): void;
  onExample(name: string): void;
}

/** "I can't hear your session yet": what's wrong, how to fix it, or try an example track. */
export function OfflineCard({ live, examples, onCheck, onExample }: OfflineProps) {
  return (
    <div className="card">
      <h2>I can't hear your session yet</h2>
      <div className="quiet">I need to read your tracks before I can give advice that fits them. Here's what I checked.</div>
      <LiveChecks live={live} />
      <button className="secondary" type="button" onClick={onCheck}>
        Check again
      </button>
      {examples.length > 0 && (
        <>
          <div className="divider">or</div>
          <div className="label">Try it on an example track</div>
          <div className="examples">
            {examples.map((t) => (
              <button key={t.name} className="key" onClick={() => onExample(t.name)}>
                {t.name}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

interface SettingsProps {
  settings: Settings;
  testResult: string | null;
  live: LiveStatus;
  hubVersion: string | null;
  theme: Theme;
  onSave: ModelFormProps["onSave"];
  onTest(): void;
  onCheckLive(): void;
  onBackToLive(): void;
  onTheme(theme: Theme): void;
  usage: UsageReport | null;
  usageRange: UsageRange;
  onUsageRange(range: UsageRange): void;
  onOpenChat(chatId: string): void;
}

export function SettingsPage(props: SettingsProps) {
  const { settings, testResult, live, hubVersion, theme } = props;
  return (
    <div className="settings">
      <section aria-labelledby="s-model">
        <div className="label" id="s-model">
          Model
        </div>
        <ModelForm settings={settings} testResult={testResult} onSave={props.onSave} onTest={props.onTest} />
      </section>
      <UsageSection
        report={props.usage}
        range={props.usageRange}
        provider={settings.provider}
        onRange={props.onUsageRange}
        onOpenChat={props.onOpenChat}
      />
      <section aria-labelledby="s-live">
        <div className="label" id="s-live">
          Ableton Live
        </div>
        <div className="row-between">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className={`led ${live.connected ? "ok" : live.example ? "on" : "bad"}`} />
            {live.connected
              ? "Connected through AbletonOSC"
              : live.example
                ? `Using the example track ${live.example}`
                : (live.detail ?? "Not connected")}
          </div>
          {live.example ? (
            <button className="secondary" onClick={props.onBackToLive}>
              Use Live
            </button>
          ) : (
            <button className="secondary" onClick={props.onCheckLive}>
              Check
            </button>
          )}
        </div>
        <div className="hint mono">localhost · send 11000 · listen 11001</div>
      </section>
      <section aria-labelledby="s-panel">
        <div className="label" id="s-panel">
          Panel
        </div>
        <div className="segmented" role="group" aria-label="Theme">
          {(["system", "dark", "light"] as const).map((t) => (
            <button key={t} type="button" aria-pressed={theme === t} onClick={() => props.onTheme(t)}>
              {t === "system" ? "Match Mac" : t === "dark" ? "Dark" : "Light"}
            </button>
          ))}
        </div>
      </section>
      <div className="settings-foot">
        <span>vibe2knob {hubVersion ?? "0.1"} · open source</span>
        <a href="https://github.com/thi-mb/vibe2knob" target="_blank" rel="noreferrer">
          GitHub
        </a>
      </div>
    </div>
  );
}
