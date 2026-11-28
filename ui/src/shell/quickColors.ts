/**
 * Quick choice of a colour (Э27, 27.6, Д-43 А4): ten standard colours — three of the brand palette
 * (docs/brand/README.md: Copper Orange, Cobalt, Slate, Anthracite) and the basic ones of geology — and the five
 * colours of the user chosen last, the same for all windows. The values live here and nowhere else: the swatches and
 * the commands take them from this list.
 */

export interface StandardColor {
  id: string;
  /** Name in Russian: the hint of a swatch is «Красный #D32F2F». */
  name: string;
  /** `#rrggbb`, upper case. */
  hex: string;
}

/** Brand palette (docs/brand/README.md; the dark theme of `styles/tokens.css`). CSS cannot be imported as values:
 * `ui/test/quickColors.test.ts` reads the table of the brand book and the tokens and holds these four to them. */
export const BRAND = { copper: "#F46A1A", cobalt: "#1F6BFF", slate: "#8A99A8", anthracite: "#1B222B" } as const;

export const STANDARD_COLORS: readonly StandardColor[] = [
  { id: "red", name: "Красный", hex: "#D32F2F" },
  { id: "orange", name: "Оранжевый", hex: BRAND.copper },
  { id: "yellow", name: "Жёлтый", hex: "#F2C500" },
  { id: "green", name: "Зелёный", hex: "#2E9D4F" },
  { id: "cyan", name: "Голубой", hex: "#2BB3E6" },
  { id: "blue", name: "Синий", hex: BRAND.cobalt },
  { id: "purple", name: "Фиолетовый", hex: "#7B3FBF" },
  { id: "brown", name: "Коричневый", hex: "#7A4B2A" },
  { id: "gray", name: "Серый", hex: BRAND.slate },
  { id: "black", name: "Чёрный", hex: BRAND.anthracite },
];

export const RECENT_COLORS_MAX = 5;

const HEX = /^#[0-9a-f]{6}$/i;

/** `#rrggbb` in lower case, or null for anything else. */
export function normalizeHex(raw: unknown): string | null {
  return typeof raw === "string" && HEX.test(raw.trim()) ? raw.trim().toLowerCase() : null;
}

/** The standard colour of a value, if it is one. */
export function standardOf(raw: unknown): StandardColor | null {
  const hex = normalizeHex(raw);
  return hex ? (STANDARD_COLORS.find((c) => c.hex.toLowerCase() === hex) ?? null) : null;
}

/** The hint of a swatch: the name and the code — «Красный #D32F2F»; a colour of the user — «Свой цвет #A1B2C3». */
export function colorTitle(raw: string): string {
  const std = standardOf(raw);
  const hex = (normalizeHex(raw) ?? raw).toUpperCase();
  return `${std ? std.name : "Свой цвет"} ${hex}`;
}

/** The history after a colour was chosen: it comes first, without repeats, five at most. A colour that is standard has
 * its own place on the palette and is not kept; a value that is not a colour changes nothing. */
export function pushRecent(history: readonly string[], raw: unknown): string[] {
  const hex = normalizeHex(raw);
  if (!hex || standardOf(hex)) return [...history];
  return [hex, ...history.filter((c) => c !== hex)].slice(0, RECENT_COLORS_MAX);
}

/** The history as it was stored: colours only, without repeats, five at most (a damaged value — an empty list). */
export function normalizeRecent(raw: unknown): string[] {
  const out: string[] = [];
  if (Array.isArray(raw)) for (const c of raw) {
    const hex = normalizeHex(c);
    if (hex && !out.includes(hex)) out.push(hex);
  }
  return out.slice(0, RECENT_COLORS_MAX);
}

// ---- the history of the session of the interface, shared by all windows ----

export const RECENT_STORAGE_KEY = "bedrockforge2.recentColors";

let recent: string[] | null = null;
const listeners = new Set<() => void>();

function stored(): string[] {
  try {
    return normalizeRecent(JSON.parse(localStorage.getItem(RECENT_STORAGE_KEY) ?? "[]"));
  } catch {
    return []; // no storage (a private window, tests): the history lives until the window is closed
  }
}

/** The recent colours of the user, the newest first. The same array until the history changes. */
export function recentColors(): readonly string[] {
  if (recent === null) recent = stored();
  return recent;
}

/** A colour the user chose: it goes first, the windows that show the palette are told. */
export function rememberColor(raw: unknown): void {
  const next = pushRecent(recentColors(), raw);
  if (next.length === recent!.length && next.every((c, i) => c === recent![i])) return;
  recent = next;
  try {
    localStorage.setItem(RECENT_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* not kept for the next start */
  }
  for (const l of listeners) l();
}

export function subscribeRecent(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** For tests: forgets the history (and what was stored). */
export function resetRecentColors(): void {
  recent = [];
  try {
    localStorage.removeItem(RECENT_STORAGE_KEY);
  } catch {
    /* no storage */
  }
  for (const l of listeners) l();
}
