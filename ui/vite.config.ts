import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The hub serves the built UI itself. In development, `npm run dev` serves the UI with hot
// reload and forwards the WebSocket to a running hub.
const HUB = process.env.VIBE2KNOB_HUB ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/ws": { target: HUB, ws: true } },
  },
});
