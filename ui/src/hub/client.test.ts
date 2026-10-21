import { describe, expect, it } from "vitest";
import { HubClient, type SocketLike } from "./client";

class FakeSocket implements SocketLike {
  readyState = 1;
  sent: string[] = [];
  onopen: SocketLike["onopen"] = null;
  onclose: SocketLike["onclose"] = null;
  onmessage: SocketLike["onmessage"] = null;
  send(data: string) {
    this.sent.push(data);
  }
  close() {}
}

function connected() {
  const socket = new FakeSocket();
  const hub = new HubClient("ws://test/ws", () => socket);
  hub.connect();
  return { hub, socket };
}

describe("HubClient", () => {
  it("stamps the protocol version and a request id on what it sends", () => {
    const { hub, socket } = connected();
    const id = hub.send({ type: "chat.send", chat_id: "c1", text: "wider" });
    expect(JSON.parse(socket.sent[0])).toEqual({
      type: "chat.send",
      chat_id: "c1",
      text: "wider",
      v: 1,
      request_id: id,
    });
  });

  it("routes hub messages to handlers by type", () => {
    const { hub, socket } = connected();
    const texts: string[] = [];
    hub.on("chat.delta", (m) => texts.push(m.text));
    socket.onmessage!({ data: JSON.stringify({ v: 1, type: "chat.delta", chat_id: "c1", turn_id: "t1", text: "Spread" }) });
    socket.onmessage!({ data: JSON.stringify({ v: 1, type: "chat.done", chat_id: "c1", turn_id: "t1" }) });
    expect(texts).toEqual(["Spread"]);
  });

  it("ignores messages from a different protocol version", () => {
    const { hub, socket } = connected();
    let calls = 0;
    hub.on("chat.done", () => calls++);
    socket.onmessage!({ data: JSON.stringify({ v: 2, type: "chat.done", chat_id: "c1", turn_id: "t1" }) });
    expect(calls).toBe(0);
  });
});
