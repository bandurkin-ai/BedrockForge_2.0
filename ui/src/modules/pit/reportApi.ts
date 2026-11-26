/**
 * The client of the report pit-by-pit (ТЗ 8.11.4; ADR 0028 решения 18, 19; docs/progress/e21-api.md §5, §7): `GET /pit/scenarios/{id}/report?calendar=` — a view of the stored
 * result; the calendar is a JSON text `{"q_ore": 1e6, "q_mass": null, "rate": 0.1}` (none — the calendar of the scenario); the answer is read again from the same result, nothing is written.
 * 404 `pit_scenario_not_found`; 409 `pit_no_result` (the scenario has no result: «Запустите расчёт сценария»). No React.
 */
import { api, CoreError } from "../../shell/api";
import { readReport, type Calendar, type PitReport } from "./reportModel";

/** The calendar of a request: all three keys (a gap is `null`: no limit / no NPV). */
export function calendarQuery(c: Calendar): string {
  return `?calendar=${encodeURIComponent(JSON.stringify({ q_ore: c.q_ore, q_mass: c.q_mass, rate: c.rate }))}`;
}

export async function loadReport(id: number, calendar?: Calendar | null): Promise<PitReport> {
  const r = readReport(await api.get(`/pit/scenarios/${id}/report${calendar ? calendarQuery(calendar) : ""}`));
  if (!r) throw new CoreError(0, "bad_answer", "Ядро вернуло отчёт pit-by-pit в непонятной форме.", "Обновите программу.");
  return r;
}
