/**
 * Saved charts of a window (ТЗ 7.28; ADR 0019 §2): the title of the window follows the name of the settings.
 */
import { useEffect } from "react";
import { updateWindow } from "../../shell/windows";
import type { ChartWindowProps } from "./bind";
import { chartName, windowTitle, type ChartKind, type SettingsOf } from "./settings";

export function useSavedChart<K extends ChartKind>(kind: K, props: ChartWindowProps<K>, settings: SettingsOf[K], _set: (s: SettingsOf[K]) => void): { note: string | null } {
  const name = chartName(kind, settings);
  useEffect(() => updateWindow(props.winId, { title: windowTitle(name, props.setName) }), [props.winId, name, props.setName]);
  return { note: null };
}
