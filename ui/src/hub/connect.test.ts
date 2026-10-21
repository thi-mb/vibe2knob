import { describe, expect, it } from "vitest";
import { hubTarget } from "./connect";

function memory() {
  const data = new Map<string, string>();
  return { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) };
}

const loc = (search: string) => ({ protocol: "http:", host: "127.0.0.1:8765", search, pathname: "/", hash: "" });

describe("hubTarget", () => {
  it("moves the token from the address bar into the socket URL and session storage", () => {
    const storage = memory();
    let replaced = "";
    const t = hubTarget(loc("?token=abc"), storage, (u) => (replaced = u), false);
    expect(t).toEqual({ url: "ws://127.0.0.1:8765/ws?token=abc", fake: null });
    expect(replaced).toBe("/");
    // A reload has no token in the URL but still connects with it.
    expect(hubTarget(loc(""), storage, () => {}, false).url).toBe("ws://127.0.0.1:8765/ws?token=abc");
  });

  it("keeps other parameters and picks the fake hub's scenario", () => {
    let replaced = "";
    const t = hubTarget(loc("?token=abc&fake=offline"), memory(), (u) => (replaced = u), false);
    expect(t.fake).toBe("offline");
    expect(replaced).toBe("/?fake=offline");
    expect(hubTarget(loc("?fake"), memory(), () => {}, false).fake).toBe("chat");
  });
});
