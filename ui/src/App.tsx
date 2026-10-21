import { useEffect, useState } from "react";
import { HubClient } from "./hub/client";
import type { LiveStatus } from "./protocol/types";

// Placeholder shell: connects to the hub and shows Live's status. The panel itself
// (tabs, context chips, step cards, settings, setup card) lands in its own PR.
export function App() {
  const [live, setLive] = useState<LiveStatus | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const hub = new HubClient(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    hub.onOpen(() => {
      setConnected(true);
      hub.send({ type: "hello", client: "browser" });
    });
    hub.onClose(() => setConnected(false));
    hub.on("welcome", (m) => setLive(m.live));
    hub.on("live.status", (m) => setLive(m.live));
    hub.connect();
    return () => hub.close();
  }, []);

  return (
    <main style={{ padding: 16, fontFamily: "var(--font-mono)" }}>
      <h1 style={{ fontSize: 16, margin: 0 }}>vibe2knob</h1>
      <p style={{ color: "var(--text-quiet)" }}>
        hub {connected ? "connected" : "offline"} · live{" "}
        {live?.connected ? (live.selected_track?.name ?? "connected") : "offline"}
      </p>
    </main>
  );
}
