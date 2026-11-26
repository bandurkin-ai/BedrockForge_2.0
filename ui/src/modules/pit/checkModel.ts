/**
 * The result of the check of the template (`pit.check`; ТЗ 8.11.2, 8.11.5; ADR 0028 решения 4, 9–12; docs/progress/e21-api.md §4) as the window «Проверка шаблона»
 * shows it: K, the levels, the offsets and the angle error of every domain, the effective angle φ_эфф(az) at 360 azimuths (degrees from the north, clockwise,
 * in the axes of the project), the warning «увеличьте K». A gap in φ_эфф is `null` (the line breaks), never 0. The CSV of the chart. Pure; no React.
 */
import { csvText } from "../../charts/core/csv";
import { list, num, obj, readWarnings, str, type SectorParam, type Warning } from "./answers";

export interface CheckDomain {
  /** null — the common table (all the blocks); else the label of the domain */
  label: string | null;
  table: SectorParam[];
  offsets: number | null;
  errorMax: number | null;
  errorMean: number | null;
  /** the error is above the tolerance (not a refusal: a warning) */
  warning: boolean;
  /** the azimuths of `phi`, degrees (0…359) */
  az: number[];
  /** φ_эфф at `az`; null — no value */
  phi: (number | null)[];
}

export interface CheckResult {
  model: { id: number | null; name: string } | null;
  k: number | null;
  kAuto: boolean;
  tolerance: number | null;
  levels: number | null;
  domains: CheckDomain[];
  warnings: Warning[];
  seconds: number | null;
}

const sector = (v: unknown): SectorParam | null => {
  const s = obj(v);
  const angle = num(s.angle);
  return angle === null ? null : { az: num(s.az) ?? 0, angle };
};

/** `check_result` of the core (the result of the job `pit.check`; also `sidecar:template`). An answer that is not a check is null. */
export function readCheck(raw: unknown): CheckResult | null {
  const r = obj(raw);
  if (!Array.isArray(r.domains)) return null;
  const m = obj(r.model);
  return {
    model: Object.keys(m).length ? { id: num(m.id), name: str(m.name) ?? "" } : null,
    k: num(r.k),
    kAuto: r.k_auto === true,
    tolerance: num(r.tolerance),
    levels: num(r.levels),
    domains: r.domains.map((x): CheckDomain => {
      const d = obj(x);
      const e = obj(d.error);
      const phi = obj(d.phi_eff);
      const angle = list(phi.angle).map((v) => num(v));
      const az = list(phi.az).map((v) => num(v));
      const offsets = list(d.offsets).length;
      return {
        label: str(d.label),
        table: list(d.table).map(sector).filter((s): s is SectorParam => s !== null),
        offsets: num(d.n_offsets) ?? (offsets ? offsets : null),
        errorMax: num(e.max),
        errorMean: num(e.mean),
        warning: d.warning === true,
        az: angle.map((_, i) => az[i] ?? i),
        phi: angle,
      };
    }),
    warnings: readWarnings(r.warnings),
    seconds: num(r.seconds),
  };
}

/** The name of a domain for people: the label, else «Все блоки». */
export const domainTitle = (d: Pick<CheckDomain, "label">): string => d.label ?? "Все блоки";

/** The tolerance fits? — `error.max` against the tolerance (the core says it in `warning`; here for the words under the table). */
export const exceeds = (d: CheckDomain, tolerance: number | null): boolean => d.warning || (tolerance !== null && d.errorMax !== null && d.errorMax > tolerance);

/** The range of the angle axis: all φ_эфф and the angles of the tables, rounded out to whole degrees (5° at least wide); null — nothing to show. */
export function phiRange(domains: readonly CheckDomain[]): { min: number; max: number } | null {
  let lo = Infinity;
  let hi = -Infinity;
  const see = (v: number | null) => {
    if (v === null || !Number.isFinite(v)) return;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  };
  for (const d of domains) {
    for (const v of d.phi) see(v);
    for (const s of d.table) see(s.angle);
  }
  if (lo === Infinity) return null;
  lo = Math.floor(lo - 2);
  hi = Math.ceil(hi + 2);
  if (hi - lo < 5) {
    const mid = (hi + lo) / 2;
    lo = mid - 2.5;
    hi = mid + 2.5;
  }
  return { min: lo, max: hi };
}

/** The index of the azimuth nearest to `az` on the circle (360 and 0 are one direction). */
export function nearestAz(az: readonly number[], x: number): number {
  let best = 0;
  let bestD = Infinity;
  for (let i = 0; i < az.length; i++) {
    const d = Math.abs(((az[i] - x + 540) % 360) - 180);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  return best;
}

/** φ_эфф of a domain at the azimuth of a sector of its table (the nearest whole degree); null — no value. */
export function phiAt(d: CheckDomain, az: number): number | null {
  return d.az.length ? (d.phi[nearestAz(d.az, az)] ?? null) : null;
}

/** The rows of the table of the sectors of a domain: the azimuth, the angle of the table, φ_эфф there and the deviation (null — no value). */
export function sectorRows(d: CheckDomain): { az: number; angle: number; phi: number | null; delta: number | null }[] {
  return d.table.map((s) => {
    const phi = phiAt(d, s.az);
    return { az: s.az, angle: s.angle, phi, delta: phi === null ? null : phi - s.angle };
  });
}

/** The data of the chart: «Азимут; φ_эфф домена 1; …»; a gap stays an empty cell. */
export function phiCsv(c: CheckResult): string {
  const n = Math.max(0, ...c.domains.map((d) => d.az.length));
  const head = ["Азимут, °", ...c.domains.map((d) => `φ_эфф, °: ${domainTitle(d)}`)];
  const rows = Array.from({ length: n }, (_, i) => [c.domains.find((d) => d.az[i] !== undefined)?.az[i] ?? i, ...c.domains.map((d) => d.phi[i] ?? null)]);
  return csvText(head, rows);
}
