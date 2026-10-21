import { arcPath, knobPoint, type KnobTarget } from "../lib/knob";

/** A knob glyph whose arc marks the target range and whose pointer shows where it is now. */
export function Knob({ target, size = 30 }: { target: KnobTarget; size?: number }) {
  const pointerAt = target.current ?? (target.low + target.high) / 2;
  const [px, py] = knobPoint(pointerAt, 8);
  return (
    <svg width={size} height={size} viewBox="0 0 44 44" aria-hidden="true">
      <path className="knob-track" d={arcPath(0, 1)} fill="none" strokeWidth="3.5" strokeLinecap="round" />
      <path className="knob-arc" d={arcPath(target.low, target.high)} fill="none" strokeWidth="3.5" strokeLinecap="round" />
      <circle className="knob-cap" cx="22" cy="22" r="10" />
      <path className="knob-pointer" d={`M22 22 L${px.toFixed(2)} ${py.toFixed(2)}`} strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}
