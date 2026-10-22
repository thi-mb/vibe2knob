import { useCallback, useEffect, useMemo, useState } from "react";
import { Composer, Conversation } from "./components/Chat";
import { ContextBar, Tabs } from "./components/Context";
import { Icon } from "./components/Icon";
import { OfflineCard, SettingsPage, SetupCard, type Theme } from "./components/Screens";
import { ChatUsageBar } from "./components/Usage";
import { load, save } from "./lib/storage";
import type { ContextSet } from "./protocol/types";
import { activeChat, canSeeLive, isGeneral, isStreaming, type PanelState } from "./state/store";
import { useHub, type HubActions } from "./state/useHub";

const QUICK_REPLIES = ["too much", "not enough", "I made the changes"];

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => load<Theme>("theme", "system"));
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
  }, [theme]);
  return [
    theme,
    (t) => {
      save("theme", t);
      setTheme(t);
    },
  ];
}

function StatusLight({ state }: { state: PanelState }) {
  if (state.connection !== "open") {
    return (
      <span className="status" role="status">
        <span className={`led ${state.connection === "rejected" ? "bad" : "on blink"}`} />
        {state.connection === "rejected" ? "No hub" : "Hub…"}
      </span>
    );
  }
  const { live } = state;
  return (
    <span className="status" role="status">
      <span className={`led ${live.connected ? "ok" : live.example ? "on" : "bad"}`} />
      {live.connected ? "Live" : live.example ? "Example" : "No Live"}
    </span>
  );
}

function HubDown({ state }: { state: PanelState }) {
  if (state.connection === "rejected") {
    return (
      <div className="full-message">
        <div className="card">
          <h2>This panel's link has expired</h2>
          <div className="quiet">
            The hub didn't accept this window's token, which happens after the hub restarts. Run{" "}
            <code className="mono">vibe2knob panel</code> again to open a fresh one.
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="full-message">
      <div className="card">
        <h2>{state.connection === "offline" ? "The hub isn't running" : "Connecting to the hub…"}</h2>
        <div className="quiet">
          {state.connection === "offline" ? (
            <>
              Start it with <code className="mono">vibe2knob panel</code>. This page reconnects on its own.
            </>
          ) : (
            "One moment."
          )}
        </div>
      </div>
    </div>
  );
}

export function App() {
  const [state, actions] = useHub();
  const [theme, setTheme] = useTheme();
  const [pickerOpen, setPickerOpen] = useState(false);
  // Decided once, when the hub first answers: a fresh install with no key gets the setup
  // card, and keeps it until it's finished even after the key is saved.
  const [setupOpen, setSetupOpen] = useState<boolean | null>(null);
  useEffect(() => {
    if (state.ready && setupOpen === null) {
      setSetupOpen(!load<boolean>("setupDone", false) && !state.settings.api_key_set);
    }
  }, [state.ready, setupOpen, state.settings.api_key_set]);

  const finishSetup = useCallback(() => {
    save("setupDone", true);
    setSetupOpen(false);
  }, []);

  const showSetup = state.ready && setupOpen === true;
  const settingsOpen = state.view === "settings";
  // The usage report is fetched each time Settings opens, so it includes the latest answers.
  useEffect(() => {
    if (state.ready && settingsOpen) actions.loadUsage();
  }, [state.ready, settingsOpen, actions]);

  return (
    <div className="panel">
      <header className="topbar">
        {settingsOpen ? (
          <div style={{ display: "flex", alignItems: "center", gap: 4, marginLeft: -10 }}>
            <button className="icon-button" aria-label="Back to chats" onClick={() => actions.show("chat")}>
              <Icon name="back" size={18} />
            </button>
            <span className="wordmark">SETTINGS</span>
          </div>
        ) : (
          <span className="wordmark">VIBE2KNOB</span>
        )}
        <div className="status">
          <StatusLight state={state} />
          {!settingsOpen && state.ready && !showSetup && (
            <button className="icon-button" aria-label="Settings" onClick={() => actions.show("settings")}>
              <Icon name="gear" size={18} />
            </button>
          )}
        </div>
      </header>

      {!state.ready ? (
        <HubDown state={state} />
      ) : showSetup ? (
        <SetupCard
          settings={state.settings}
          testResult={state.testResult}
          live={state.live}
          examples={state.examples}
          onSave={actions.updateSettings}
          onTest={actions.testSettings}
          onExample={actions.useExample}
          onFinish={finishSetup}
        />
      ) : settingsOpen ? (
        <SettingsPage
          settings={state.settings}
          testResult={state.testResult}
          live={state.live}
          hubVersion={state.hubVersion}
          theme={theme}
          onSave={actions.updateSettings}
          onTest={actions.testSettings}
          onCheckLive={actions.listTracks}
          onBackToLive={() => actions.useExample(null)}
          onTheme={setTheme}
          usage={state.usage}
          usageRange={state.usageRange}
          onUsageRange={actions.loadUsage}
          onOpenChat={actions.selectChat}
        />
      ) : (
        <ChatView state={state} actions={actions} pickerOpen={pickerOpen} setPickerOpen={setPickerOpen} />
      )}
    </div>
  );
}

interface ChatViewProps {
  state: PanelState;
  actions: HubActions;
  pickerOpen: boolean;
  setPickerOpen(open: boolean): void;
}

function ChatView({ state, actions, pickerOpen, setPickerOpen }: ChatViewProps) {
  const chat = activeChat(state);
  const context: ContextSet = chat?.context ?? { follow_selection: true, pinned: [] };
  const general = isGeneral(chat);
  const canSee = canSeeLive(state);
  const turns = chat ? state.turns[chat.id] : [];
  const streaming = isStreaming(state, chat?.id ?? null);
  const busyIds = useMemo(
    () => new Set(state.chats.filter((c) => isStreaming(state, c.id)).map((c) => c.id)),
    [state],
  );
  const trackColors = useMemo(() => new Map(state.tracks.map((t) => [t.name, t.color])), [state.tracks]);
  const blocked = !canSee && !general;
  const last = turns?.at(-1);
  const quick = last?.role === "assistant" && !last.pending && (last.steps?.length ?? 0) > 0 ? QUICK_REPLIES : [];

  const setContext = (next: ContextSet) => {
    if (chat) actions.setContext(chat.id, next);
    else actions.newChat(next);
  };

  return (
    <div className="chat-layout">
      <Tabs
        chats={state.chats}
        activeId={state.activeChatId}
        busyIds={busyIds}
        onSelect={actions.selectChat}
        onClose={actions.closeChat}
        onNew={() => actions.newChat()}
      />
      <div className="chat-main">
        {state.notice && (
          <div className="notice" role="alert">
            <span>{state.notice}</span>
            <button className="icon-button" aria-label="Dismiss" onClick={actions.dismissNotice}>
              ×
            </button>
          </div>
        )}
        <ContextBar
          context={context}
          selected={state.live.selected_track}
          tracks={state.tracks}
          canSee={canSee}
          example={state.live.example}
          pickerOpen={pickerOpen}
          onChange={setContext}
          onPicker={(open) => {
            if (open) actions.listTracks();
            setPickerOpen(open);
          }}
        />
        {chat && <ChatUsageBar chat={chat} onNewChat={() => actions.newChat(chat.context)} />}
        <Conversation
          key={chat?.id ?? "none"}
          chatId={chat?.id ?? "none"}
          turns={turns}
          trackColors={trackColors}
          onAsk={actions.send}
          after={
            blocked && (turns?.length ?? 0) > 0 ? (
              <OfflineCard live={state.live} examples={state.examples} onCheck={actions.listTracks} onExample={actions.useExample} />
            ) : null
          }
          empty={
            blocked ? (
              <OfflineCard live={state.live} examples={state.examples} onCheck={actions.listTracks} onExample={actions.useExample} />
            ) : (
              <div className="card">
                <h2>{general ? "Ask anything about Live" : "What should it feel like?"}</h2>
                <div className="quiet">
                  {general
                    ? "Setup, plugins, routing, workflow. Add a track with @ when you want advice on it."
                    : state.live.selected_track
                      ? `Say how ${state.live.selected_track.name} should sound: wider, warmer, punchier, trippier.`
                      : "Select a track in Live, then say how it should sound: wider, warmer, punchier."}
                </div>
                {state.live.example && (
                  <button className="link-button" style={{ alignSelf: "flex-start" }} onClick={() => actions.useExample(null)}>
                    Stop using the example and go back to Live
                  </button>
                )}
              </div>
            )
          }
        />
        <Composer
          disabled={blocked}
          busy={streaming}
          placeholder={
            blocked
              ? "Connect Live or pick an example"
              : general
                ? "Ask anything about Live… @ to add a track"
                : "Describe the vibe… wider, warmer, trippier"
          }
          quickReplies={quick}
          onSend={actions.send}
          onAt={() => {
            actions.listTracks();
            setPickerOpen(true);
          }}
        />
      </div>
    </div>
  );
}
