import { useCallback, useEffect, useState } from "react";

export type Tab = "table" | "plan" | "section" | "log" | "view3d" | "audit" | "import";
export type TableKind = "collar" | "survey" | "assay";

export interface Route {
  tab: Tab;
  kind: TableKind;
  hole: string | null;
}

const KINDS: TableKind[] = ["collar", "survey", "assay"];
// URL segment <-> tab
const SEGMENTS: Record<string, Tab> = {
  table: "table",
  plan: "plan",
  section: "section",
  log: "log",
  "3d": "view3d",
  audit: "audit",
  import: "import",
};
const SEGMENT_OF: Record<Tab, string> = {
  table: "table", plan: "plan", section: "section", log: "log", view3d: "3d", audit: "audit", import: "import",
};

export const DEFAULT_ROUTE: Route = { tab: "import", kind: "collar", hole: null };

/** "#/table/assay?hole=H-1" -> { tab: "table", kind: "assay", hole: "H-1" }. Unknown input falls back to the defaults. */
export function parseRoute(hash: string): Route {
  const [path = "", query = ""] = hash.replace(/^#\/?/, "").split("?");
  const [segment = "", kindPart = ""] = path.split("/");
  const tab = SEGMENTS[segment] ?? DEFAULT_ROUTE.tab;
  const kind = (KINDS as string[]).includes(kindPart) ? (kindPart as TableKind) : DEFAULT_ROUTE.kind;
  const hole = new URLSearchParams(query).get("hole") || null;
  return { tab, kind, hole };
}

export function formatRoute(route: Route): string {
  const base = `#/${SEGMENT_OF[route.tab]}`;
  const path = route.tab === "table" ? `${base}/${route.kind}` : base;
  return route.hole ? `${path}?${new URLSearchParams({ hole: route.hole })}` : path;
}

export interface NavigateOptions {
  /** Replace the current history entry instead of adding one (used for hole selection). */
  replace?: boolean;
}

/** Route state kept in location.hash, so reload and browser back/forward keep the place. */
export function useRoute(): [Route, (next: Partial<Route>, options?: NavigateOptions) => void] {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = useCallback(
    (next: Partial<Route>, options: NavigateOptions = {}) => {
      // Read the live hash: several navigations can happen before React re-renders.
      const target = formatRoute({ ...parseRoute(window.location.hash), ...next });
      if (options.replace) {
        window.history.replaceState(null, "", target);
        setRoute(parseRoute(target));
      } else {
        window.location.hash = target;
      }
    },
    [],
  );
  return [route, navigate];
}
