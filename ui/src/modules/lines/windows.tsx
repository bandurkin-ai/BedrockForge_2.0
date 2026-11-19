/**
 * The floating windows of the string editor (ТЗ 7.0.12), drawn by the window layer of the shell: the question
 * with several answers, «Правка выбранного», «Версии», «Перенести в другой слой». Null — not a window of the
 * editor (`Shell.tsx` calls this for kinds it does not know).
 */
import type { ReactNode } from "react";
import type { FloatWin } from "../../shell/windows";
import { ChoiceWindow } from "./ChoiceWindow";
import type { ModifyAction } from "./modify";
import { ModifyWindow } from "./ModifyWindow";
import { MoveLinesWindow } from "./MoveLinesWindow";
import { VersionsWindow } from "./VersionsWindow";

export const LINES_WINDOWS = ["lines-choice", "lines-modify", "lines-versions", "lines-move"];

export function renderLinesWindow(w: FloatWin): ReactNode {
  switch (w.kind) {
    case "lines-choice":
      return <ChoiceWindow choice={Number(w.props?.choice)} />;
    case "lines-modify":
      return <ModifyWindow winId={w.id} action={w.props?.action as ModifyAction} />;
    case "lines-versions":
      return <VersionsWindow layerId={Number(w.props?.layerId)} />;
    case "lines-move":
      return <MoveLinesWindow winId={w.id} layerId={Number(w.props?.layerId)} />;
    default:
      return null;
  }
}
