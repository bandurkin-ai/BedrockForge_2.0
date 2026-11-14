import { useCallback, useEffect, useState } from "react";

export type TableKind = "collar" | "survey" | "assay";

/** What the address remembers: the active drillhole set, the selected hole and the open table. */
export interface Route {
  set: number | null;
  hole: string | null;
  kind: TableKind;
}

const KINDS: TableKind[] = ["collar", "survey", "assay"];

export const DEFAULT_ROUTE: Route = { set: null, hole: null, kind: "collar" };

/** "#/?set=3&hole=H-1&table=assay" -> { set: 3, hole: "H-1", kind: "assay" }. Unknown input falls back to the defaults. */
export function parseRoute(hash: string): Route {
  const query = hash.replace(/^#\/?/, "").split("?")[1] ?? "";
  const params = new URLSearchParams(query);
  const set = Number(params.get("set"));
  const table = params.get("table") ?? "";
  return {
    set: Number.isInteger(set) && set > 0 ? set : null,
    hole: params.get("hole") || null,
    kind: (KINDS as string[]).includes(table) ? (table as TableKind) : DEFAULT_ROUTE.kind,
  };
}

export function formatRoute(route: Route): string {
  const params = new URLSearchParams();
  if (route.set !== null) params.set("set", String(route.set));
  if (route.hole) params.set("hole", route.hole);
  if (route.kind !== DEFAULT_ROUTE.kind) params.set("table", route.kind);
  const query = params.toString();
  return query ? `#/?${query}` : "#/";
}

/** Route state kept in location.hash, so reload and the browser history keep the place. */
export function useRoute(): [Route, (next: Partial<Route>) => void] {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = useCallback((next: Partial<Route>) => {
    // Read the live hash: several navigations can happen before React re-renders.
    const target = formatRoute({ ...parseRoute(window.location.hash), ...next });
    window.history.replaceState(null, "", target);
    setRoute(parseRoute(target));
  }, []);
  return [route, navigate];
}
