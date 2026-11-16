/** Groups of the data tree in the order the user wants them (dragged by their title rows). */
export const TREE_GROUPS = ["cat:drillholes", "cat:wireframe", "cat:dtm", "cat:lines", "cat:rasters", "cat:blocks"] as const;

const STORAGE_KEY = "bedrockforge.treeOrder";

/** A stored order made whole: unknown keys dropped, groups added since then put at their usual place's end. */
export function normalizeOrder(raw: unknown): string[] {
  const known = new Set<string>(TREE_GROUPS);
  const seen = new Set<string>();
  const order: string[] = [];
  for (const key of Array.isArray(raw) ? raw : []) {
    if (typeof key === "string" && known.has(key) && !seen.has(key)) {
      seen.add(key);
      order.push(key);
    }
  }
  for (const key of TREE_GROUPS) {
    if (seen.has(key)) continue;
    // A new group goes after the one it follows by default (ЦТМ after the wireframes).
    const before = TREE_GROUPS[TREE_GROUPS.indexOf(key) - 1];
    const at = before ? order.indexOf(before) : -1;
    order.splice(at + 1, 0, key);
  }
  return order;
}

/** The order after `from` is dropped on `to`: before it, or after it. */
export function moveGroup(order: string[], from: string, to: string, after: boolean): string[] {
  if (from === to || !order.includes(from) || !order.includes(to)) return order;
  const next = order.filter((key) => key !== from);
  next.splice(next.indexOf(to) + (after ? 1 : 0), 0, from);
  return next;
}

export function loadOrder(): string[] {
  try {
    return normalizeOrder(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null"));
  } catch {
    return [...TREE_GROUPS];
  }
}

export function saveOrder(order: string[]): void {
  try {
    if (order.join() === TREE_GROUPS.join()) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, JSON.stringify(order));
  } catch {
    /* the order then lives until the page is closed */
  }
}
