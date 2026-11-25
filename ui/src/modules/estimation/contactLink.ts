/**
 * The saved charts of the contact analysis of the pair of domains of the soft boundary (Д-28, ТЗ 8.8.4 step 2): the wizard offers them
 * as links, the person looks at the chart and decides d_м — nothing is derived from it. A chart is of the pair when it is of the same
 * set, of the same field of the domain, along the holes (not «до каркаса») and has one of the domain values of the form as A and one
 * of the neighbouring domains as B, or the other way round. No React.
 */
import type { ChartRow } from "../charts/api";
import { normalizeSettings } from "../charts/settings";
import type { EstimationForm } from "./form";

export function contactChartsOf(charts: readonly ChartRow[], f: Pick<EstimationForm, "setId" | "domain" | "soft">): ChartRow[] {
  const dom = f.domain;
  if (f.setId === null || !dom?.field || !f.soft.on || !f.soft.domains.length) return [];
  const own = new Set(dom.values);
  const near = new Set(f.soft.domains);
  return charts
    .filter((c) => {
      if (c.kind !== "contact" || c.set_id === null || c.set_id !== f.setId) return false;
      const s = normalizeSettings("contact", c.settings, c.set_id);
      if (s.wireframe_id !== null || !s.a || !s.b || (s.domain ?? "").toLowerCase() !== dom.field.toLowerCase()) return false;
      return (own.has(s.a) && near.has(s.b)) || (own.has(s.b) && near.has(s.a));
    })
    .sort((a, b) => a.name.localeCompare(b.name, "ru"));
}
