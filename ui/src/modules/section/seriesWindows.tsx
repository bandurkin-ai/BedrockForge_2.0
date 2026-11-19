/**
 * The floating windows of series of sections, drawn by the window layer of the shell: «Новая серия» and the
 * parameters of a series (`series-edit`), «Менеджер серий» (`series-manager`). Null — not a window of series
 * (`Shell.tsx` asks this before the windows of the string editor).
 */
import type { ReactNode } from "react";
import type { FloatWin } from "../../shell/windows";
import { SERIES_MANAGER, SERIES_WINDOW } from "./seriesActions";
import type { SeriesForm } from "./seriesForm";
import { SeriesManager } from "./SeriesManager";
import { SeriesWindow } from "./SeriesWindow";

export function renderSeriesWindow(w: FloatWin): ReactNode {
  switch (w.kind) {
    case SERIES_WINDOW: {
      const p = w.props ?? {};
      const mode = p.mode === "edit" ? "edit" : "create";
      return <SeriesWindow winId={w.id} mode={mode} form={p.form as SeriesForm | undefined} seriesId={typeof p.seriesId === "number" ? p.seriesId : undefined} />;
    }
    case SERIES_MANAGER:
      return <SeriesManager selected={typeof w.props?.selected === "number" ? w.props.selected : undefined} />;
    default:
      return null;
  }
}
