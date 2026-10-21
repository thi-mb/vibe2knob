import type { ClientEnvelope, HubEnvelope } from "../protocol/types";

export const PROTOCOL_VERSION = 1;

type HubType = HubEnvelope["type"];
type HubOf<T extends HubType> = Extract<HubEnvelope, { type: T }>;
type Handler<T extends HubType> = (message: HubOf<T>) => void;

/** The minimal WebSocket surface the client uses, so tests can pass a fake. */
export interface SocketLike {
  readyState: number;
  send(data: string): void;
  close(): void;
  onopen: ((ev: unknown) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
}

/** A typed connection to the hub. Every surface (panel, browser, phone) uses this. */
export class HubClient {
  private socket: SocketLike | null = null;
  private handlers = new Map<HubType, Set<(m: HubEnvelope) => void>>();
  private openHandlers = new Set<() => void>();
  private closeHandlers = new Set<() => void>();
  private nextId = 1;

  constructor(
    private readonly url: string,
    private readonly makeSocket: (url: string) => SocketLike = (u) => new WebSocket(u) as unknown as SocketLike,
  ) {}

  connect(): void {
    const socket = this.makeSocket(this.url);
    socket.onopen = () => this.openHandlers.forEach((h) => h());
    socket.onclose = () => this.closeHandlers.forEach((h) => h());
    socket.onmessage = (ev) => this.dispatch(ev.data);
    this.socket = socket;
  }

  close(): void {
    this.socket?.close();
    this.socket = null;
  }

  /** Sends a message and returns its request_id, which the hub echoes on its reply. */
  send(message: ClientEnvelope): string {
    if (!this.socket) throw new Error("Not connected to the hub");
    const request_id = message.request_id ?? `r${this.nextId++}`;
    this.socket.send(JSON.stringify({ ...message, v: PROTOCOL_VERSION, request_id }));
    return request_id;
  }

  on<T extends HubType>(type: T, handler: Handler<T>): () => void {
    let set = this.handlers.get(type);
    if (!set) this.handlers.set(type, (set = new Set()));
    const h = handler as (m: HubEnvelope) => void;
    set.add(h);
    return () => set.delete(h);
  }

  onOpen(handler: () => void): void {
    this.openHandlers.add(handler);
  }

  onClose(handler: () => void): void {
    this.closeHandlers.add(handler);
  }

  private dispatch(data: unknown): void {
    if (typeof data !== "string") return;
    let message: HubEnvelope;
    try {
      message = JSON.parse(data) as HubEnvelope;
    } catch {
      return;
    }
    if (message.v !== PROTOCOL_VERSION) {
      console.warn(`Hub speaks protocol v${message.v}, this UI speaks v${PROTOCOL_VERSION}`);
      return;
    }
    this.handlers.get(message.type)?.forEach((h) => h(message));
  }
}
