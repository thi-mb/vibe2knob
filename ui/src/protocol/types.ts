/* Generated from src/vibe2knob/protocol.py by npm run gen:types. Do not edit. */

/**
 * Any message a client may send.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ClientEnvelope".
 */
export type ClientEnvelope =
  | Hello
  | ChatCreate
  | ChatSend
  | ChatUpdate
  | ChatOpen
  | ChatClose
  | TracksList
  | SettingsUpdate
  | SettingsTest
  | LiveUseExample
  | UsageGet;
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
  | ChatHistory
  | ChatDelta
  | ChatSteps
  | ChatDone
  | SettingsChanged
  | UsageReport
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
 * Ask for a chat's turns, e.g. when a tab is shown after a reload or reconnect.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ChatOpen".
 */
export interface ChatOpen {
  /**
   * Protocol version.
   */
  v?: 1;
  /**
   * Set by the client; echoed on the hub's reply or error.
   */
  request_id?: string | null;
  type: "chat.open";
  chat_id: string;
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
 * Use an example track instead of Live (null goes back to Live). The hub answers with
 * live.status, so every client sees the switch.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "LiveUseExample".
 */
export interface LiveUseExample {
  /**
   * Protocol version.
   */
  v?: 1;
  /**
   * Set by the client; echoed on the hub's reply or error.
   */
  request_id?: string | null;
  type: "live.use_example";
  /**
   * One of welcome.examples' names, or null.
   */
  name: string | null;
}
/**
 * Ask for the usage report, e.g. when the Usage section in Settings opens.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "UsageGet".
 */
export interface UsageGet {
  /**
   * Protocol version.
   */
  v?: 1;
  /**
   * Set by the client; echoed on the hub's reply or error.
   */
  request_id?: string | null;
  type: "usage.get";
  range?: "today" | "7d" | "30d" | "all";
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
  /**
   * Example tracks to try while Live is offline.
   */
  examples: TrackRef[];
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
  /**
   * Everything the chat used, titles included. Its model is the answers' model.
   */
  usage: Usage | null;
  /**
   * What naming the chat used.
   */
  title_usage: Usage | null;
  /**
   * Tokens the next message resends: the last answer's prompt plus its output.
   */
  context_tokens: number | null;
  /**
   * The model's context window.
   */
  context_window: number | null;
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
 * Tokens used by one turn, one chat or a period. Counted on the hub, never sent anywhere.
 *
 * input_tokens is the uncached input only: the whole prompt is input + cache_read +
 * cache_write. thinking_tokens are part of output_tokens.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "Usage".
 */
export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  thinking_tokens: number;
  /**
   * Model calls counted; each one resends the instructions.
   */
  calls: number;
  /**
   * Prompt and answer split into parts. Only instructions, tools and thinking are exact; the rest are estimates by text length.
   */
  parts: {
    [k: string]: number;
  };
  /**
   * The model that answered; null if several.
   */
  model: string | null;
  /**
   * Estimated from the hub's price table; null if no price is known.
   */
  cost_usd: number | null;
  /**
   * False when some calls had no known price, so cost_usd is low.
   */
  cost_complete: boolean;
  /**
   * The part of cost_usd spent on prompts (input and cache).
   */
  prompt_cost_usd: number | null;
  /**
   * The part of cost_usd spent on output, thinking included.
   */
  output_cost_usd: number | null;
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
  /**
   * Name of the example track in use instead of Live, if any.
   */
  example: string | null;
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
  /**
   * Device names in chain order.
   */
  devices: string[];
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
 * A chat's turns, oldest first, in answer to chat.open.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ChatHistory".
 */
export interface ChatHistory {
  /**
   * Protocol version.
   */
  v: 1;
  type: "chat.history";
  request_id: string | null;
  chat_id: string;
  turns: ChatTurn[];
}
/**
 * One turn of a saved chat, as chat.history replays it.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "ChatTurn".
 */
export interface ChatTurn {
  /**
   * For an assistant turn, the turn_id its deltas carried.
   */
  id: string;
  role: "user" | "assistant";
  text: string;
  /**
   * ISO 8601 time the turn started.
   */
  created_at: string;
  /**
   * Null when no steps were suggested.
   */
  steps: Step[] | null;
  listen_for: string | null;
  caution: string | null;
  warnings: string[];
  /**
   * Why the turn failed, if it did.
   */
  error: string | null;
  /**
   * Set on assistant turns.
   */
  usage: Usage | null;
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
  /**
   * What the turn used, if it got that far.
   */
  usage: Usage | null;
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
 * Usage for a range, in answer to usage.get. Days run oldest first, with no gaps.
 *
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "UsageReport".
 */
export interface UsageReport {
  /**
   * Protocol version.
   */
  v: 1;
  type: "usage.report";
  request_id: string | null;
  range: "today" | "7d" | "30d" | "all";
  /**
   * Today, 7 and 30 days, and all time.
   */
  totals: {
    [k: string]: Usage;
  };
  days: UsageDay[];
  by_model: UsageGroup[];
  by_kind: UsageGroup[];
  /**
   * Most expensive first.
   */
  chats: UsageChat[];
  /**
   * Share of the prompt served from cache, 0 to 1.
   */
  cache_hit_rate: number | null;
  /**
   * When the price table was last checked, YYYY-MM-DD.
   */
  prices_checked_on: string;
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "UsageDay".
 */
export interface UsageDay {
  /**
   * Local date, YYYY-MM-DD.
   */
  date: string;
  usage: Usage;
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "UsageGroup".
 */
export interface UsageGroup {
  /**
   * A model id, or a kind: chat, title or ask.
   */
  key: string;
  usage: Usage;
}
/**
 * This interface was referenced by `Vibe2KnobProtocol`'s JSON-Schema
 * via the `definition` "UsageChat".
 */
export interface UsageChat {
  chat_id: string;
  title: string | null;
  /**
   * False for a chat whose tab was closed.
   */
  open: boolean;
  usage: Usage;
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
  /**
   * Set when a chat's turn failed.
   */
  chat_id: string | null;
  /**
   * The failed turn; chat.done for it follows.
   */
  turn_id: string | null;
}
