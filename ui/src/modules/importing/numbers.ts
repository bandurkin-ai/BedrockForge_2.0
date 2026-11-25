/** Numbers typed by the user as «12,5», «12.5», «−5», «1 250,5» (ТЗ 7.19): shared by the wizards of import. No React. */

const NUMBER = /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i;

/**
 * A number typed with a comma or a point: «12,5», «12.5», «−5», «1 250,5». Empty — null (not set, never 0);
 * anything else — undefined (not a number).
 */
export function parseNumber(text: string): number | null | undefined {
  const t = text.replace(/[\s\u00a0\u202f]/g, "").replace(/\u2212/g, "-");
  if (!t) return null;
  const plain = t.replace(",", ".");
  if (!NUMBER.test(plain)) return undefined;
  const v = Number(plain);
  return Number.isFinite(v) ? v : undefined;
}
