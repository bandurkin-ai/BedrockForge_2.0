/**
 * ODA File Converter of «Настройка › Внешние программы» (ADR 0013 §5–§6; ТЗ 7.35 Д-13, Д-17): DWG is read and
 * written through this program of the user. Its path lives in the user settings (`odaConverter`), as the number
 * of job workers does: the interface sends it to the core (`PUT /tools/oda`) when the core comes up and when the
 * user changes it; the core looks for the program — the path, then BF_ODA_CONVERTER, PATH, «Program Files\ODA» —
 * and answers with the state and its Russian texts. Pure module: no React.
 */
import { api } from "./api";
import { normalizeOdaPath } from "./settings";
import type { AppState, Store } from "./store";

/** The answer of `GET` and `PUT /tools/oda` (core `importing/tools.py`). */
export interface OdaStatus {
  /** the path of «Настройка» as the core holds it */
  configured: string | null;
  found: boolean;
  /** the program DWG import and export will run */
  path: string | null;
  /** "configured" — the path itself or the program in its folder; "auto" — found by the search */
  source: "configured" | "auto" | null;
  /** what the search finds without the path */
  auto: string | null;
  version: string | null;
  message: string;
  warning: string;
  hint: string;
  /** how to get the program: free, Open Design Alliance */
  install: string;
}

type SettingsStore = Pick<Store<AppState>, "get" | "set">;

export type OdaTone = "ok" | "warn" | "bad" | "plain";

/** The state under the field, line by line (how to install is a block of its own: `hint` or `install`). */
export function odaLines(s: OdaStatus): { tone: OdaTone; text: string }[] {
  const out: { tone: OdaTone; text: string }[] = [{ tone: !s.found ? "bad" : s.warning ? "warn" : "ok", text: s.message }];
  if (s.found && s.path) out.push({ tone: "plain", text: `${s.source === "auto" ? "Найдена автоматически" : "Программа"}: ${s.path}` });
  if (s.found && s.warning) out.push({ tone: "warn", text: s.warning });
  return out;
}

/** «Проверить», «Выбрать…», «Очистить»: the path goes to the core; once the core took it, into the settings. */
export async function setOdaPath(store: SettingsStore, raw: string | null): Promise<OdaStatus> {
  const path = normalizeOdaPath(raw);
  const status = await api.put<OdaStatus>("/tools/oda", { path });
  store.set({ odaConverter: path });
  return status;
}

/** The tab opens: the core holds what the settings hold (a restarted core has forgotten the path). */
export function syncOda(store: SettingsStore): Promise<OdaStatus> {
  return api.put<OdaStatus>("/tools/oda", { path: store.get().odaConverter });
}

/** «Найти»: what the search of the core finds becomes the path; nothing found — the state as it is. */
export async function findOda(store: SettingsStore): Promise<OdaStatus> {
  const status = await api.get<OdaStatus>("/tools/oda");
  return status.auto ? setOdaPath(store, status.auto) : status;
}

/** The core came up: a path of the settings goes to it. Never fails — the start of the app does not depend on it. */
export async function pushOdaPath(store: SettingsStore): Promise<OdaStatus | null> {
  const path = store.get().odaConverter;
  if (!path) return null;
  try {
    return await api.put<OdaStatus>("/tools/oda", { path });
  } catch {
    return null;
  }
}
