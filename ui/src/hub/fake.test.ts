import { describe, expect, it } from "vitest";
import type { HubEnvelope } from "../protocol/types";
import { HubClient } from "./client";
import { FakeHub } from "./fake";

function collect(hub: HubClient) {
  const got: HubEnvelope[] = [];
  for (const t of ["welcome", "chat.changed", "chat.delta", "chat.steps", "chat.done", "chat.history"] as const) {
    hub.on(t, (m) => got.push(m));
  }
  return got;
}

const until = async (check: () => boolean) => {
  for (let i = 0; i < 100 && !check(); i++) await new Promise((r) => setTimeout(r, 5));
  expect(check()).toBe(true);
};

describe("FakeHub", () => {
  it("speaks the protocol through HubClient: welcome, then a streamed answer", async () => {
    const fake = new FakeHub({ scenario: "chat", wordDelay: 0 });
    const client = new HubClient("ws://fake/ws", () => fake.socket());
    const got = collect(client);
    client.onOpen(() => client.send({ type: "hello", client: "browser" }));
    client.connect();
    await until(() => got.some((m) => m.type === "welcome"));

    client.send({ type: "chat.send", chat_id: "c-pad", text: "make it wider" });
    await until(() => got.some((m) => m.type === "chat.done"));
    const types = got.map((m) => m.type);
    expect(types.indexOf("chat.delta")).toBeLessThan(types.indexOf("chat.steps"));
    expect(types.indexOf("chat.steps")).toBeLessThan(types.indexOf("chat.done"));

    client.send({ type: "chat.open", chat_id: "c-pad" });
    await until(() => got.some((m) => m.type === "chat.history"));
    const history = got.find((m) => m.type === "chat.history");
    expect(history?.type === "chat.history" && history.turns.length).toBe(4);
  });
});
