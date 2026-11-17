/** Numbers in forms and labels (ported from BF stats/chartMath.ts). */
export function formatNumber(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value === 0) return "0";
  const abs = Math.abs(value);
  if (abs >= 1e6 || abs < 1e-3) return value.toExponential(1).replace("e+", "e");
  return String(Number(value.toPrecision(digits)));
}

/** A form field: empty or invalid gives null (the default then applies); a decimal comma is accepted. */
export function parseNumber(text: string): number | null {
  const n = Number(text.trim().replace(",", "."));
  return text.trim() === "" || !Number.isFinite(n) ? null : n;
}
