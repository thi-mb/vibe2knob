/* Generated from src/vibe2knob/protocol.py by npm run gen:types. Do not edit. */

/**
 * Any message a client may send.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ClientEnvelope".
 */
export type ClientEnvelope =
  Hello | ChatCreate | ChatSend | ChatUpdate | ChatClose | TracksList | SettingsUpdate | SettingsTest;
/**
 * Any message the hub may send.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "HubEnvelope".
 */
export type HubEnvelope =
  | Welcome
  | LiveStatusChanged
  | Tracks
  | ChatChanged
  | ChatClosed
  | ChatDelta
  | ChatSteps
  | ChatDone
  | SettingsChanged
  | ErrorMessage;

/**
 * vibe2knob hub protocol v1
 */
export interface Vibe2KnobProtocol {
  client?: ClientEnvelope;
  hub?: HubEnvelope;
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "Hello".
 */
export interface Hello {
  /**
   * Protocol version.
   */
  v?: 1;
  /**
   * Set by the client; echoed on the hub's reply or error.
   */
  request_id?: string | null;
  type: "hello";
  client: "panel" | "browser" | "phone";
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ChatCreate".
 */
export interface ChatCreate {
  /**
   * Protocol version.
   */
  v?: 1;
  /**
   * Set by the client; echoed on the hub's reply or error.
   */
  request_id?: string | null;
  type: "chat.create";
  context?: ContextSetInput;
}
/**
 * What a chat looks at, besides the light session summary it always gets.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ContextSetInput".
 */
export interface ContextSetInput {
  /**
   * Include whichever track is selected in Live.
   */
  follow_selection?: boolean;
  /**
   * Track indexes pinned with @.
   */
  pinned?: number[];
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ChatSend".
 */
export interface ChatSend {
  /**
   * Protocol version.
   */
  v?: 1;
  /**
   * Set by the client; echoed on the hub's reply or error.
   */
  request_id?: string | null;
  type: "chat.send";
  chat_id: string;
  text: string;
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ChatUpdate".
 */
export interface ChatUpdate {
  /**
   * Protocol version.
   */
  v?: 1;
  /**
   * Set by the client; echoed on the hub's reply or error.
   */
  request_id?: string | null;
  type: "chat.update";
  chat_id: string;
  title?: string | null;
  context?: ContextSetInput | null;
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ChatClose".
 */
export interface ChatClose {
  /**
   * Protocol version.
   */
  v?: 1;
  /**
   * Set by the client; echoed on the hub's reply or error.
   */
  request_id?: string | null;
  type: "chat.close";
  chat_id: string;
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "TracksList".
 */
export interface TracksList {
  /**
   * Protocol version.
   */
  v?: 1;
  /**
   * Set by the client; echoed on the hub's reply or error.
   */
  request_id?: string | null;
  type: "tracks.list";
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "SettingsUpdate".
 */
export interface SettingsUpdate {
  /**
   * Protocol version.
   */
  v?: 1;
  /**
   * Set by the client; echoed on the hub's reply or error.
   */
  request_id?: string | null;
  type: "settings.update";
  provider?: ("claude" | "ollama") | null;
  model?: string | null;
  /**
   * Stored in the Keychain, never echoed.
   */
  api_key?: string | null;
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "SettingsTest".
 */
export interface SettingsTest {
  /**
   * Protocol version.
   */
  v?: 1;
  /**
   * Set by the client; echoed on the hub's reply or error.
   */
  request_id?: string | null;
  type: "settings.test";
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "Welcome".
 */
export interface Welcome {
  /**
   * Protocol version.
   */
  v: 1;
  type: "welcome";
  hub_version: string;
  chats: ChatSummary[];
  live: LiveStatus;
  settings: Settings;
}
/**
 * A chat as a tab shows it.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ChatSummary".
 */
export interface ChatSummary {
  id: string;
  /**
   * Topic title; null until named.
   */
  title: string | null;
  context: ContextSet;
  /**
   * ISO 8601 time of the last change.
   */
  updated_at: string;
}
/**
 * What a chat looks at, besides the light session summary it always gets.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ContextSet".
 */
export interface ContextSet {
  /**
   * Include whichever track is selected in Live.
   */
  follow_selection: boolean;
  /**
   * Track indexes pinned with @.
   */
  pinned: number[];
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "LiveStatus".
 */
export interface LiveStatus {
  connected: boolean;
  selected_track: TrackRef | null;
  /**
   * Why Live isn't reachable, if it isn't.
   */
  detail: string | null;
}
/**
 * A track as the session list and the context chips show it.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "TrackRef".
 */
export interface TrackRef {
  index: number;
  name: string;
  type: "audio" | "midi" | "group" | "return" | "master";
  color: number | null;
}
/**
 * Settings as the client sees them. The API key itself never leaves the hub.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "Settings".
 */
export interface Settings {
  provider: "claude" | "ollama";
  model: string | null;
  api_key_set: boolean;
}
/**
 * Sent when Live connects or disconnects, or the selected track changes.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "LiveStatusChanged".
 */
export interface LiveStatusChanged {
  /**
   * Protocol version.
   */
  v: 1;
  type: "live.status";
  live: LiveStatus;
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "Tracks".
 */
export interface Tracks {
  /**
   * Protocol version.
   */
  v: 1;
  type: "tracks";
  request_id: string | null;
  tracks: TrackRef[];
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ChatChanged".
 */
export interface ChatChanged {
  /**
   * Protocol version.
   */
  v: 1;
  type: "chat.changed";
  request_id: string | null;
  chat: ChatSummary;
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ChatClosed".
 */
export interface ChatClosed {
  /**
   * Protocol version.
   */
  v: 1;
  type: "chat.closed";
  request_id: string | null;
  chat_id: string;
}
/**
 * A piece of the assistant's prose, streamed as it is written.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ChatDelta".
 */
export interface ChatDelta {
  /**
   * Protocol version.
   */
  v: 1;
  type: "chat.delta";
  chat_id: string;
  turn_id: string;
  text: string;
}
/**
 * The steps the model suggested. Optional: a prose-only answer sends none.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ChatSteps".
 */
export interface ChatSteps {
  /**
   * Protocol version.
   */
  v: 1;
  type: "chat.steps";
  chat_id: string;
  turn_id: string;
  steps: Step[];
  listen_for: string | null;
  caution: string | null;
  warnings: string[];
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "Step".
 */
export interface Step {
  action: "add_device" | "adjust" | "enable" | "disable" | "remove" | "reorder";
  /**
   * Track the step is on, as named in the chat's context; null for the only track.
   */
  track?: string | null;
  /**
   * Exact name as listed on the chain, or the stock device name when adding one.
   */
  device: string;
  /**
   * Rack chain the device is in, or null.
   */
  chain?: string | null;
  /**
   * True when the device is already on the track.
   */
  on_track: boolean;
  /**
   * Parameter as labelled in Live, or null.
   */
  parameter: string | null;
  /**
   * Starting point, e.g. '120-140 %'.
   */
  value_range: string | null;
  /**
   * What it does and why it fits, one or two sentences.
   */
  explanation: string;
  /**
   * Stock device this step uses; filled in by vibe2knob's checks.
   */
  stock_device?: string | null;
  /**
   * Live's index of the track the step is on; filled in by vibe2knob's checks, null when the track name is missing or matches more than one track.
   */
  track_index?: number | null;
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ChatDone".
 */
export interface ChatDone {
  /**
   * Protocol version.
   */
  v: 1;
  type: "chat.done";
  chat_id: string;
  turn_id: string;
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "SettingsChanged".
 */
export interface SettingsChanged {
  /**
   * Protocol version.
   */
  v: 1;
  type: "settings";
  request_id: string | null;
  settings: Settings;
  /**
   * Outcome of a settings.test.
   */
  test_result: string | null;
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ErrorMessage".
 */
export interface ErrorMessage {
  /**
   * Protocol version.
   */
  v: 1;
  type: "error";
  request_id: string | null;
  code: "bad_request" | "unsupported_version" | "not_found" | "provider" | "live" | "internal";
  message: string;
}
