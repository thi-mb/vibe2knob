// Turns a step's free-text value_range ("120-140 %", "200-300 Hz", "-4 to -12 dB
// (currently -6 dB)") into a target arc on a knob. Anything we can't read confidently
// gets no knob: the card then shows the text alone.

export interface KnobTarget {
  /** Target range as fractions of the knob's travel, 0..1, low <= high. */
  low: number;
  high: number;
  /** Where the knob is now, if the text says so. */
  current: number | null;
  /** Short label, e.g. "120–140 %". */
  label: string;
}

type Unit = "%" | "Hz" | "dB" | "ms" | "raw";

interface Scale {
  toFraction(value: number): number;
}

const clamp = (x: number) => Math.min(1, Math.max(0, x));
const linear = (min: number, max: number): Scale => ({ toFraction: (v) => clamp((v - min) / (max - min)) });
const log = (min: number, max: number): Scale => ({
  toFraction: (v) => clamp(Math.log(Math.max(v, min) / min) / Math.log(max / min)),
});

const NUM = String.raw`[+-]?\d+(?:\.\d+)?`;
const UNIT = String.raw`\s*(%|k?Hz|dB|ms|s)?`;
// "A-B unit", "A to B unit", "A unit to B unit"; an optional "(currently X)" or "(from X)".
const RANGE = new RegExp(
  String.raw`^(${NUM})${UNIT}\s*(?:-|–|to)\s*(${NUM})${UNIT}\s*(?:\((?:currently|from)\s+(${NUM})${UNIT}\))?\s*$`,
  "i",
);

function normalise(value: number, unit: string | undefined): [number, Unit | null] {
  switch (unit?.toLowerCase()) {
    case "%":
      return [value, "%"];
    case "hz":
      return [value, "Hz"];
    case "khz":
      return [value * 1000, "Hz"];
    case "db":
      return [value, "dB"];
    case "ms":
      return [value, "ms"];
    case "s":
      return [value * 1000, "ms"];
    default:
      return [value, null];
  }
}

function scaleFor(unit: Unit, high: number): Scale {
  switch (unit) {
    case "%":
      return high > 100 ? linear(0, 200) : linear(0, 100);
    case "Hz":
      return log(20, 20000);
    case "dB":
      return linear(-48, 12);
    case "ms":
      return log(1, 10000);
    case "raw":
      return linear(0, 1);
  }
}

export function parseTarget(valueRange: string | null | undefined): KnobTarget | null {
  if (!valueRange) return null;
  const m = RANGE.exec(valueRange.trim());
  if (!m) return null;
  const [, aText, aUnit, bText, bUnit, curText, curUnit] = m;
  const unitText = bUnit ?? aUnit;
  let [a, unitA] = normalise(Number(aText), aUnit ?? unitText);
  let [b, unitB] = normalise(Number(bText), unitText);
  if (unitA !== unitB) return null;
  let unit: Unit | null = unitA;
  if (unit === null) {
    // Bare numbers only make sense as raw 0..1 plugin values.
    if (a < 0 || a > 1 || b < 0 || b > 1) return null;
    unit = "raw";
  }
  if (a > b) [a, b] = [b, a];
  const scale = scaleFor(unit, b);
  let current: number | null = null;
  if (curText !== undefined) {
    const [c, cu] = normalise(Number(curText), curUnit ?? unitText);
    if (cu === unitA) current = scale.toFraction(c);
  }
  const shown = unitText ? ` ${unitText}` : "";
  const signed = /^[+-]/.test(aText) || /^[+-]/.test(bText);
  return {
    low: scale.toFraction(a),
    high: scale.toFraction(b),
    current,
    label: `${aText}${signed ? " to " : "–"}${bText}${shown}`,
  };
}

// Knob geometry: a 270° sweep from 7:30 to 4:30, as on hardware.
const START = 225; // degrees, measured anticlockwise from 3 o'clock
const SWEEP = 270;

export function knobPoint(fraction: number, radius: number, cx = 22, cy = 22): [number, number] {
  const angle = ((START - SWEEP * fraction) * Math.PI) / 180;
  return [cx + radius * Math.cos(angle), cy - radius * Math.sin(angle)];
}

export function arcPath(from: number, to: number, radius = 16): string {
  const [x1, y1] = knobPoint(from, radius);
  const [x2, y2] = knobPoint(Math.max(to, from + 0.01), radius);
  const large = (to - from) * SWEEP > 180 ? 1 : 0;
  return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${radius} ${radius} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}
