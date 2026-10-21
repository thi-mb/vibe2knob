import type { TrackRef } from "../protocol/types";

/** Live's track colour (0xRRGGBB as an int) as CSS, or a neutral swatch when it's unset. */
export function trackColor(color: number | null | undefined): string {
  if (color === null || color === undefined) return "var(--line-strong)";
  return `#${color.toString(16).padStart(6, "0")}`;
}

/** "Wavetable → Chorus-Ensemble → Utility", or "No devices". */
export function deviceChain(track: Pick<TrackRef, "devices"> | null | undefined): string {
  const devices = track?.devices ?? [];
  return devices.length ? devices.join(" → ") : "No devices";
}
