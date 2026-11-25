/**
 * The handlers of `blocks.density`, `blocks.classify` and `blocks.scheme.save` (ТЗ 8.7.5; ADR 0023 §9, decision 13, № 68):
 * the density (a constant, a field, a table «домен → плотность», a formula; the density «по умолчанию»; the field of the
 * source) and the categories (a scheme of the project or the rules, the field of the estimate, the wireframes of the
 * categories, the majority filter) — both start the job of the core over the version of the model and follow it (`run.ts`);
 * the schemes of the project (`GET/PUT /blocks/schemes`: the version of a scheme, 409 — asked in our own window, 423).
 * A command without the model and the way (or the scheme) opens the window on its tab. No React.
 */
import type { App } from "../../../shell/app";
import type { CommandSource } from "../../../shell/commands";
import { askConfirm } from "../../../shell/dialogs";
import { CoreError } from "../../../shell/api";
import { refusal } from "../../../shell/objectRun";
import { errorText } from "../../../shell/props";
import { blockById } from "../state";
import { checkExpr, exprIssueOf, loadSchemes, putScheme, refusalOf, startClassify, startDensity } from "./api";
import { chooseModel, openFieldsWindow, windowOfFields } from "./bind";
import { formOfArgs as classifyFormOfArgs, pickScheme, requestOfArgs as classifyRequest, schemeRequestOfArgs } from "./classifyForm";
import { given, issueOfRefusal, type Args } from "./common";
import { formOfArgs as densityFormOfArgs, requestOfArgs as densityRequest } from "./densityForm";
import { notFound, READ_ONLY, runOp } from "./run";
import { fieldsStore, setClassify, setIssue, setSchemes } from "./state";

// ---- the density ----

export async function runDensity(app: App, a: Args, source: CommandSource): Promise<string> {
  if (!given(a, "model_id") || !given(a, "method")) {
    openFieldsWindow(app, "density", typeof a.model_id === "number" ? a.model_id : undefined);
    return "Открыто окно «Поля блочной модели»: плотность";
  }
  const got = densityRequest(a);
  if (!got.ok) {
    setIssue(got.issue);
    throw refusal("bad_param", got.issue.message, got.issue.hint);
  }
  const { modelId, body } = got.request;
  const model = blockById(modelId);
  if (!model) throw notFound(modelId);
  if (source === "user" || windowOfFields()) openFieldsWindow(app, "density", modelId);
  else chooseModel(modelId);
  fieldsStore.set((s) => ({ density: densityFormOfArgs(a, s.density) })); // after the model: another model empties the choices of fields
  return runOp(
    app,
    {
      op: "density",
      tab: "density",
      modelId,
      field: body.out,
      place: "out",
      replace: body.replace === true,
      restrictOn: body.restrict !== undefined,
      filterId: body.filter_id ?? null,
      start: (version, replace) => startDensity(modelId, { ...body, expected_version: version, ...(replace ? { replace: true } : {}) }),
    },
    source,
  );
}

// ---- the schemes ----

/** Reads the schemes of the project; a form with no rules yet takes the rules of its scheme. */
export async function refreshSchemes(): Promise<void> {
  try {
    const list = await loadSchemes();
    setSchemes({ list, loaded: true, error: null });
    const f = fieldsStore.get().classify;
    if (!f.rules.length && f.schemeId) setClassify(pickScheme(f, list, f.schemeId));
  } catch (e) {
    setSchemes({ loaded: true, error: errorText(e) });
  }
}

export const schemeConflictQuestion = (name: string) => ({
  title: "Схема изменена в другом окне",
  message: `Схему «${name}» изменили в другом окне, пока вы её правили. Записать правила этого окна поверх изменений? Иначе схемы будут перечитаны, а правила этого окна останутся в таблице.`,
  okLabel: "Записать поверх",
  danger: true,
});

export async function runSchemeSave(app: App, a: Args, source: CommandSource): Promise<string> {
  if (!given(a, "id") && !given(a, "rules")) {
    openFieldsWindow(app, "classify");
    return "Открыто окно «Поля блочной модели»: категории и схемы";
  }
  if (app.store.get().projectReadOnly) throw READ_ONLY;
  const got = schemeRequestOfArgs(a, fieldsStore.get().schemes.list);
  if (!got.ok) {
    setIssue(got.issue);
    throw refusal("bad_param", got.issue.message, got.issue.hint);
  }
  const { id, name, rules } = got.request;
  let version = got.request.version;
  for (let tries = 0; tries < 2; tries++) {
    try {
      const saved = await putScheme(id, { name, rules, ...(version !== undefined ? { expected_version: version } : {}) });
      await refreshSchemes();
      const f = fieldsStore.get().classify;
      if (f.schemeId === id || f.editId === id) setClassify({ ...pickScheme(f, fieldsStore.get().schemes.list, id), schemeId: id });
      return `Схема «${saved.name}» (${saved.id}) сохранена: правил ${saved.rules.length}, версия ${saved.version}`;
    } catch (e) {
      const r = refusalOf(e);
      if (r.code === "version_conflict" && tries === 0) {
        const current = e instanceof CoreError && typeof e.body?.version === "number" ? e.body.version : undefined;
        await refreshSchemes();
        if (source === "user" && current !== undefined && (await askConfirm(schemeConflictQuestion(name)))) {
          version = current;
          continue;
        }
        setIssue(issueOfRefusal("classify", { ...r, hint: `${r.hint} Схемы перечитаны.`.trim() }));
        throw refusal(r.code, r.message, `${r.hint} Схемы перечитаны.`.trim());
      }
      setIssue(issueOfRefusal("classify", r));
      throw refusal(r.code, r.message, r.hint);
    }
  }
  throw refusal("operation_failed", "Схема не сохранена.", "Повторите действие.");
}

// ---- the categories ----

export async function runClassify(app: App, a: Args, source: CommandSource): Promise<string> {
  if (!given(a, "model_id") || (!given(a, "scheme") && !given(a, "rules"))) {
    openFieldsWindow(app, "classify", typeof a.model_id === "number" ? a.model_id : undefined);
    if (!fieldsStore.get().schemes.loaded) void refreshSchemes();
    return "Открыто окно «Поля блочной модели»: категории";
  }
  const got = classifyRequest(a);
  if (!got.ok) {
    setIssue(got.issue);
    throw refusal("bad_param", got.issue.message, got.issue.hint);
  }
  const { modelId, body } = got.request;
  const model = blockById(modelId);
  if (!model) throw notFound(modelId);
  if (!fieldsStore.get().schemes.loaded) await refreshSchemes();
  if (source === "user" || windowOfFields()) openFieldsWindow(app, "classify", modelId);
  else chooseModel(modelId);
  fieldsStore.set((s) => ({ classify: classifyFormOfArgs(a, s.schemes.list, s.classify) }));
  return runOp(
    app,
    {
      op: "classify",
      tab: "classify",
      modelId,
      field: body.out,
      place: "out",
      replace: body.replace === true,
      restrictOn: body.restrict !== undefined,
      filterId: body.filter_id ?? null,
      start: (version, replace) => startClassify(modelId, { ...body, expected_version: version, ...(replace ? { replace: true } : {}) }),
    },
    source,
  );
}

/** «Проверить правила»: every condition of the table on the model (`POST /blocks/calc/check`); the words of the core stand under the row. */
export async function checkRules(): Promise<void> {
  const s = fieldsStore.get();
  if (s.modelId === null) return;
  const out: Record<number, string | null> = {};
  for (const [i, r] of s.classify.rules.entries()) {
    if (!r.expr.trim()) {
      out[i] = "Условие не задано.";
      continue;
    }
    try {
      await checkExpr(s.modelId, r.expr);
      out[i] = null;
    } catch (e) {
      const x = exprIssueOf(e);
      out[i] = x ? `${x.message} ${x.hint}` : errorText(e);
    }
  }
  if (fieldsStore.get().modelId === s.modelId) fieldsStore.set({ rulesCheck: out });
}
