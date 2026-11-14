import type { ValueStats } from "../api";

export interface ScaleSettings {
  /** null = automatic (5th percentile) */
  min: number | null;
  /** null = automatic (95th percentile) */
  max: number | null;
  log: boolean;
}

export const DEFAULT_SCALE: ScaleSettings = { min: null, max: null, log: true };
export const NO_VALUE_COLOR = "#9aa3b5";

// blue -> teal -> green -> yellow -> orange -> red
const STOPS: [number, number, number][] = [
  [43, 95, 168],
  [42, 167, 160],
  [143, 209, 90],
  [242, 212, 58],
  [238, 122, 36],
  [178, 24, 43],
];

/** Default range: 5th to 95th percentile, so a few extreme values do not wash out the scale. */
export function autoRange(stats: ValueStats | null | undefined): [number, number] | null {
  if (!stats || stats.count === 0) return null;
  let lo = stats.p05 ?? stats.min;
  let hi = stats.p95 ?? stats.max;
  if (lo === null || hi === null) return null;
  if (!(hi > lo)) {
    lo = stats.min ?? lo;
    hi = stats.max ?? hi;
  }
  if (!(hi > lo)) hi = lo > 0 ? lo * 10 : lo + 1;
  return [lo, hi];
}

export class ColorScale {
  readonly min: number;
  readonly max: number;
  readonly log: boolean;

  constructor(min: number, max: number, log: boolean) {
    this.log = log;
    // A log scale needs a positive lower bound.
    this.min = log && min <= 0 ? Math.max(max / 1000, 1e-9) : min;
    this.max = max > this.min ? max : this.min * 10;
  }

  /** Position 0..1 of a value on the scale (clamped). */
  position(value: number): number {
    if (!Number.isFinite(value)) return 0;
    if (this.log) {
      const v = Math.max(value, this.min);
      return clamp01((Math.log10(v) - Math.log10(this.min)) / (Math.log10(this.max) - Math.log10(this.min)));
    }
    return clamp01((value - this.min) / (this.max - this.min));
  }

  rgb(value: number): [number, number, number] {
    const t = this.position(value) * (STOPS.length - 1);
    const i = Math.min(Math.floor(t), STOPS.length - 2);
    const f = t - i;
    return [0, 1, 2].map((c) => Math.round(STOPS[i][c] + (STOPS[i + 1][c] - STOPS[i][c]) * f)) as [number, number, number];
  }

  css(value: number | null | undefined): string {
    if (value === null || value === undefined || !Number.isFinite(value)) return NO_VALUE_COLOR;
    const [r, g, b] = this.rgb(value);
    return `rgb(${r},${g},${b})`;
  }

  /** Legend ticks: decades for a log scale, round values otherwise. */
  ticks(): { value: number; position: number }[] {
    const values: number[] = [];
    if (this.log) {
      for (let k = Math.ceil(Math.log10(this.min)); k <= Math.floor(Math.log10(this.max)); k++) values.push(10 ** k);
    } else {
      const step = niceStep(this.max - this.min, 4);
      for (let v = Math.ceil(this.min / step) * step; v <= this.max + step * 1e-9; v += step) values.push(Number(v.toPrecision(12)));
    }
    return values.map((value) => ({ value, position: this.position(value) }));
  }

  gradientCss(): string {
    const stops = STOPS.map(([r, g, b], i) => `rgb(${r},${g},${b}) ${(i / (STOPS.length - 1)) * 100}%`);
    return `linear-gradient(to right, ${stops.join(", ")})`;
  }
}

/** Scale from user settings, falling back to the automatic range; null if there is nothing to scale. */
export function makeScale(settings: ScaleSettings, stats: ValueStats | null | undefined): ColorScale | null {
  const auto = autoRange(stats);
  const min = settings.min ?? auto?.[0];
  const max = settings.max ?? auto?.[1];
  if (min === undefined || max === undefined || !(max > min)) return null;
  return new ColorScale(min, max, settings.log);
}

export function formatValue(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 100) return value.toFixed(0);
  if (abs >= 1) return value.toFixed(2);
  if (abs >= 0.01) return value.toFixed(3);
  return String(Number(value.toPrecision(2)));
}

function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v));
}

function niceStep(span: number, target: number): number {
  const raw = span / Math.max(target, 1);
  const pow = 10 ** Math.floor(Math.log10(raw));
  const f = raw / pow;
  return (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * pow;
}
