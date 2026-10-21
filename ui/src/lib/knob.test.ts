import { describe, expect, it } from "vitest";
import { parseTarget } from "./knob";

describe("parseTarget", () => {
  it("reads percent ranges, widening the scale past 100 %", () => {
    expect(parseTarget("40-55 %")).toMatchObject({ low: 0.4, high: 0.55, current: null, label: "40–55 %" });
    const width = parseTarget("120-140 %")!;
    expect(width.low).toBeCloseTo(0.6);
    expect(width.high).toBeCloseTo(0.7);
  });

  it("reads 'to' ranges, kHz, and the current value", () => {
    const t = parseTarget("4 to 8 kHz")!;
    expect(t.low).toBeLessThan(t.high);
    expect(t.label).toBe("4–8 kHz");
    const dry = parseTarget("30-40 % (currently 25 %)")!;
    expect(dry.current).toBeCloseTo(0.25);
  });

  it("orders signed dB ranges and labels them with 'to'", () => {
    const t = parseTarget("-4 to -12 dB")!;
    expect(t.low).toBeLessThan(t.high);
    expect(t.label).toBe("-4 to -12 dB");
  });

  it("treats bare numbers in 0..1 as raw plugin values", () => {
    expect(parseTarget("0.20 to 0.35")).toMatchObject({ low: 0.2, high: 0.35 });
    expect(parseTarget("4 (currently 2)")).toBeNull();
  });

  it("gives up on anything it can't read confidently", () => {
    for (const text of [
      null,
      "On, 120-150 Hz",
      "Classic to Vibrato",
      "Rate 0.3 to 0.8 Hz; Amount 25 to 45 %",
      "0.4-1.0 s, with Dry/Wet at 100 % on a return track",
      "17.5 to 25-30",
    ]) {
      expect(parseTarget(text)).toBeNull();
    }
  });
});
