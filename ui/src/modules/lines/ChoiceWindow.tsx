/**
 * The own window of a question with several answers (`choice.ts`): «Слой изменён» after a 409 (save as a new
 * layer, read the layer anew, cancel), «Завершить правку» with unsaved changes (save, do not save, cancel).
 * Enter — the main answer; the cross of the window — «Отмена».
 */
import { useStore } from "../../shell/useStore";
import dlg from "../../shell/Dialog.module.css";
import css from "../../shell/PropsParts.module.css";
import { answerChoice, choiceStore } from "./choice";

export function ChoiceWindow({ choice }: { choice: number }) {
  const req = useStore(choiceStore, (s) => s.pending.find((r) => r.id === choice) ?? null);
  if (!req) return null;
  const main = req.actions.find((a) => a.primary) ?? req.actions[0];
  return (
    <div
      className={css.panel}
      data-testid="lines-choice"
      onKeyDown={(e) => {
        if (e.key === "Enter" && (e.target as HTMLElement).tagName !== "BUTTON") answerChoice(req.id, main.id);
      }}
    >
      <p className={dlg.body} data-testid="lines-choice-message">
        {req.message}
      </p>
      <div className={dlg.buttons}>
        {req.actions.map((a) => (
          <button
            key={a.id}
            type="button"
            className={`${dlg.btn} ${a.primary ? dlg.primary : a.danger ? dlg.danger : ""}`}
            data-testid={`choice-${a.id}`}
            title={a.hint}
            autoFocus={a === main}
            onClick={() => answerChoice(req.id, a.id)}
          >
            {a.label}
          </button>
        ))}
      </div>
    </div>
  );
}
