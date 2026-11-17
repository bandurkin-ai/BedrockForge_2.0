/**
 * Actions of the form on the style library (ТЗ 7.16): a style by its uid, «Сохранить как» that replaces a
 * style of the same level and name (BF putStyle), the body of a style from a layer.
 */
import type { StripLogSettings, Track } from "../../charts/striplog/model";
import { CoreError } from "../../shell/api";
import { dataKind, layerBody, paletteBody, templateBody, type DataKind, type StyleLevel, type StyleRec } from "../../viz/downhole/styles";
import type { IntervalTableInfo } from "../../viz/downhole/types";
import { createStyle, libraryStore, patchStyle, type StyleRow } from "./library";

export const errText = (e: unknown): string => (e instanceof CoreError ? e.full : e instanceof Error ? e.message : String(e));

/** The library row of a style (with its version); undefined for a built-in or a deleted one. */
export const rowOf = (uid: string | undefined): StyleRow | undefined => (uid ? libraryStore.get().styles.find((s) => s.uid === uid) : undefined);

export function needRow(style: Pick<StyleRec, "uid" | "name">): StyleRow {
  const r = rowOf(style.uid);
  if (!r) throw new Error(`Стиля «${style.name}» нет в библиотеке проекта`);
  return r;
}

/** A new style, or the one of the same level and name with this body (the name was confirmed «Заменить»). */
export async function putStyle(name: string, level: StyleLevel, kind: DataKind, body: Record<string, unknown>): Promise<StyleRow> {
  const key = name.trim().toLowerCase();
  const existing = libraryStore.get().styles.find((s) => s.level === level && s.name.trim().toLowerCase() === key);
  return existing ? patchStyle(existing, { body, data_kind: kind }) : createStyle(name.trim(), level, kind, body, []);
}

/** The body and the kind of data of a style of a level made from the display or one of its layers. */
export function styleBody(level: StyleLevel, resolved: StripLogSettings, track: Track | undefined, tables: IntervalTableInfo[]): { body: Record<string, unknown>; kind: DataKind } {
  if (level === "display") return { body: templateBody(resolved) as unknown as Record<string, unknown>, kind: "mixed" };
  if (!track) throw new Error("Выберите слой");
  const c = tables.find((x) => x.key === track.table)?.columns.find((x) => x.name === track.field);
  if (level === "layer") return { body: layerBody(track) as Record<string, unknown>, kind: dataKind(track, c) };
  const body = paletteBody(track, c?.kind === "number" && track.type !== "event");
  if (!body) throw new Error("У слоя один цвет: сохранять нечего");
  return { body: body as unknown as Record<string, unknown>, kind: c?.kind === "number" ? "number" : "text" };
}
