// «Префикс полей оценки» у «Категорий» (подшаг 18.18; ТЗ 8.7.5; вопрос № 78): поле формы, аргументы команды `blocks.classify`,
// проверка по-русски до обращения к ядру, переименование полей оценки в тексте правила для «Проверить правила».
import { describe, expect, it } from "vitest";
import { argsOfForm, EMPTY_CLASSIFY, formOfArgs, requestOfArgs, withPrefix, type ClassifyForm } from "../src/modules/blocks/fields/classifyForm";

const RULES = [{ label: "Руда", expr: "PASS = 1 AND AU >= 1" }];
const form = (patch: Partial<ClassifyForm>): ClassifyForm => ({ ...EMPTY_CLASSIFY, schemeId: "", rules: RULES, ...patch });

describe("the prefix of the fields of an estimate in the categories", () => {
  it("empty prefix — the arguments are as they were (no key)", () => {
    const got = argsOfForm(1, form({}), []);
    expect(got.ok && "prefix" in got.args).toBe(false);
    const blank = argsOfForm(1, form({ prefix: "  " }), []);
    expect(blank.ok && "prefix" in blank.args).toBe(false);
  });

  it("the prefix goes to the command and to the body of the request, trimmed", () => {
    const got = argsOfForm(1, form({ prefix: " EST_ " }), []);
    expect(got.ok && got.args.prefix).toBe("EST_");
    if (!got.ok) throw new Error("form");
    const req = requestOfArgs(got.args);
    expect(req.ok && req.request.body.prefix).toBe("EST_");
    expect(formOfArgs(got.args, []).prefix).toBe("EST_");
  });

  it("a wrong prefix is told in Russian with a hint and the place, before the core is asked", () => {
    for (const prefix of ["AU OK", "ЗОЛОТО OK", "x".repeat(41), "a-b"]) {
      const got = argsOfForm(1, form({ prefix }), []);
      expect(got.ok, prefix).toBe(false);
      if (got.ok) continue;
      expect(got.issue).toMatchObject({ tab: "classify", place: "prefix", param: "prefix" });
      expect(got.issue.message).toMatch(/Префикс полей оценки/);
      expect(got.issue.hint).not.toBe("");
    }
    const req = requestOfArgs({ model_id: 1, rules: JSON.stringify(RULES), prefix: "a b" });
    expect(req.ok).toBe(false);
    if (!req.ok) expect(req.issue).toMatchObject({ place: "prefix", param: "prefix" });
    expect(requestOfArgs({ model_id: 1, rules: JSON.stringify(RULES), prefix: "x".repeat(40) }).ok).toBe(true);
  });

  it("a prefix of any letters is accepted, like the prefix of an estimate («Золото» gives «ЗОЛОТО_OK_»)", () => {
    for (const prefix of ["ЗОЛОТО_OK_", "Au_ok_", "Я".repeat(40)]) {
      const got = argsOfForm(1, form({ prefix }), []);
      expect(got.ok && got.args.prefix, prefix).toBe(prefix);
      expect(requestOfArgs({ model_id: 1, rules: JSON.stringify(RULES), prefix }).ok, prefix).toBe(true);
    }
  });

  it("withPrefix renames only the fields of an estimate, as the core does", () => {
    expect(withPrefix("PASS = 1 AND slope >= 0.8 AND AU > 1", "EST_")).toBe("EST_PASS = 1 AND EST_SLOPE >= 0.8 AND AU > 1");
    expect(withPrefix("[nh] >= 2 OR p3 > 0.5 OR 'PASS' = 'PASS'", "Z_")).toBe("[Z_NH] >= 2 OR Z_P3 > 0.5 OR 'PASS' = 'PASS'");
    expect(withPrefix("PASS = 1", "")).toBe("PASS = 1");
    expect(withPrefix("[Поле с пробелом] > 0", "Z_")).toBe("[Поле с пробелом] > 0");
  });
});
