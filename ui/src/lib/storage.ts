// Per-viewer conveniences only (theme, setup seen, ticked steps). Any of it may be missing,
// so every read has a default and every access is guarded.

const PREFIX = "vibe2knob.";

export function load<T>(key: string, fallback: T): T {
  try {
    const raw = globalThis.localStorage?.getItem(PREFIX + key);
    return raw === null || raw === undefined ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function save(key: string, value: unknown): void {
  try {
    globalThis.localStorage?.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    // Storage is blocked or full: the setting just won't stick.
  }
}
