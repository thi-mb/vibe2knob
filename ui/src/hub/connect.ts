// Where the panel's hub lives and how it authenticates.
//
// The hub opens http://127.0.0.1:8765/?token=XYZ. We keep the token for this tab in
// sessionStorage, take it out of the address bar, and send it on the WebSocket URL.
// `?fake` (or VITE_FAKE_HUB=1) swaps in the scripted fake hub, for development without a
// hub and for the Playwright screenshots.

const TOKEN_KEY = "vibe2knob.token";

interface LocationLike {
  protocol: string;
  host: string;
  search: string;
  pathname: string;
  hash: string;
}

export interface HubTarget {
  url: string;
  fake: string | null;
}

export function hubTarget(
  loc: LocationLike = location,
  storage: Pick<Storage, "getItem" | "setItem"> | null = safeSessionStorage(),
  replaceUrl: (url: string) => void = (url) => history.replaceState(null, "", url),
  fakeByDefault = import.meta.env?.VITE_FAKE_HUB === "1",
): HubTarget {
  const params = new URLSearchParams(loc.search);
  const fakeParam = params.get("fake");
  const fake = fakeParam !== null ? fakeParam || "chat" : fakeByDefault ? "chat" : null;

  let token = params.get("token");
  if (token) {
    try {
      storage?.setItem(TOKEN_KEY, token);
    } catch {
      // Without storage a reload needs the hub's link again; nothing else changes.
    }
    params.delete("token");
    const rest = params.toString();
    replaceUrl(`${loc.pathname}${rest ? `?${rest}` : ""}${loc.hash}`);
  } else {
    try {
      token = storage?.getItem(TOKEN_KEY) ?? null;
    } catch {
      token = null;
    }
  }

  const scheme = loc.protocol === "https:" ? "wss" : "ws";
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return { url: `${scheme}://${loc.host}/ws${query}`, fake };
}

function safeSessionStorage(): Storage | null {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}
