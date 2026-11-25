// Export of a block model (ТЗ 8.7.7, 7.35 Д-17; ADR 0023 §12; Э17 17.38) on the REAL answers of the core
// (`core/tests/data/blocks/export_window_answers.json`, made by `core/tests/test_blocks_export_window_answers.py` through
// `POST /blocks/{id}/export` and the job `blocks.export`): the form of the window → the arguments of the command → the body
// the core got, the refusals of the core in their place of the form, the failures of the job, the file written read as
// the core wrote it (no size is guessed), the text about the file; and «Свойства» of the model — the turn, the
// sub-blocks and the checks of the import — as the core tells them.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { argsOfForm, bodyOfArgs, formatOfPath, formOfArgs } from "../src/modules/blocks/export/args";
import {
  bodyOfForm,
  chosenPath,
  defaultFileName,
  EMPTY_FORM,
  fieldOfRefusal,
  FORMATS,
  issueOfRefusal,
  nextFormat,
  omfReason,
  readResult,
  resultText,
  selectionText,
  sizeText,
  type ExportBody,
  type ExportForm,
  type Format,
} from "../src/modules/blocks/export/model";
import { ruleToDraft, type Rule } from "../src/modules/blocks/filters/constructor";
import type { BlockModelInfo } from "../src/modules/blocks/api";
import { checkRows, exampleText, gridFacts, subFacts, v2 } from "../src/modules/blocks/props";

const A = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/export_window_answers.json"), "utf-8"));
const IDS = A.ids as { model: number; sub: number; filter: number };
type Run = { request: ExportBody & Record<string, unknown>; answer: { status: number; body: Record<string, unknown> }; job?: { status: string; error: { code: string; message: string; hint: string } | null; result: unknown } };
const RUNS = A.runs as Record<string, Run>;
const REFUSED = A.refused as Record<string, Run>;
const FAILED = A.failed as Record<string, Run>;
const ws = (text: string): string => text.replace(/\u00a0/g, " ");

const form = (patch: Partial<ExportForm>): ExportForm => ({ ...EMPTY_FORM, modelId: IDS.model, ...patch });
/** The form of the window that asks for the recorded request. */
const FORMS: Record<string, Partial<ExportForm>> = {
  dat: { format: "dat" },
  dat_fields: { format: "dat", allFields: false, fields: ["DOM", "AU"], emptyOn: true, empty: "-99", encoding: "bocu1" },
  dat_filtered: { format: "dat", allFields: false, fields: ["AU", "N"], filterId: IDS.filter, restrictOn: true, restrict: ruleToDraft({ cond: { field: "N", op: ">", value: 0 } } as Rule) },
  csv: { format: "csv" },
  csv_micromine: { format: "csv", header: "micromine", delimiter: ",", decimal: ".", encoding: "utf-8" },
  csv_tab: { format: "csv", delimiter: "\t", decimal: ",", encoding: "cp1251", emptyOn: true, empty: "-999" },
  parquet: { format: "parquet", allFields: false, fields: ["AU", "CU"] },
  omf: { format: "omf", allFields: false, fields: ["AU", "DOM"] },
  sub_dat: { format: "dat", modelId: IDS.sub },
  sub_parquet: { format: "parquet", modelId: IDS.sub },
  extension_added: { format: "csv" },
};
/** The recorded request without what is the default of the core: the window does not send «,» as the decimal mark. */
const asked = (name: string): ExportBody => {
  const { decimal, ...rest } = RUNS[name].request as ExportBody & { decimal?: string };
  return (decimal === "," ? rest : RUNS[name].request) as ExportBody;
};
const formOf = (name: string): ExportForm => {
  const f = form(FORMS[name]);
  const path = RUNS[name].request.path;
  return { ...f, path, pathFormat: f.format };
};

describe("the request of the form is the body the core got", () => {
  for (const name of Object.keys(FORMS)) {
    it(`${name}: the form → the body of the recorded request`, () => {
      const got = bodyOfForm(formOf(name));
      expect(got.ok).toBe(true);
      if (!got.ok) return;
      expect(got.body).toEqual(asked(name));
    });
    it(`${name}: the arguments of the command come back as the same body, the form shows them`, () => {
      const f = formOf(name);
      const args = argsOfForm(f, f.path as string);
      const body = bodyOfArgs(args);
      expect(body.ok).toBe(true);
      if (!body.ok) return;
      expect(body.body).toEqual(asked(name));
      expect(body.modelId).toBe(f.modelId);
      const shown = formOfArgs(args);
      expect(shown.ok && shown.form.format).toBe(f.format);
    });
  }

  it("the core took every recorded request and wrote the file; the extension is added by the core, not by the window", () => {
    for (const [name, run] of Object.entries(RUNS)) {
      expect(run.answer.status, name).toBe(200);
      expect(run.job?.status, name).toBe("done");
    }
    expect(RUNS.extension_added.request.path).toBe("/выгрузка/noext");
    expect((RUNS.extension_added.job!.result as { path: string }).path).toBe("/выгрузка/noext.csv"); // the window shows this path
  });

  it("the defaults of the core are not sent: «;», the decimal comma, the plain header, the encoding of the format", () => {
    const got = bodyOfForm(form({ format: "csv", path: "/a/b.csv", pathFormat: "csv" }));
    expect(got.ok && got.body).toEqual({ format: "csv", path: "/a/b.csv" });
    const dat = bodyOfForm(form({ format: "dat", path: "/a/b.dat", pathFormat: "dat", header: "micromine", delimiter: "|", decimal: ".", emptyOn: false }));
    expect(dat.ok && dat.body).toEqual({ format: "dat", path: "/a/b.dat" }); // a CSV's options do not leak into a DAT
    const parquet = bodyOfForm(form({ format: "parquet", path: "/a/b.parquet", pathFormat: "parquet", emptyOn: true, encoding: "cp1251" }));
    expect(parquet.ok && parquet.body).toEqual({ format: "parquet", path: "/a/b.parquet" }); // no code of «пусто», no encoding
  });

  it("a gap is never a zero: the code of «пусто» is sent only when asked, as a number", () => {
    const f = formOf("dat");
    expect(bodyOfForm({ ...f, emptyOn: false, empty: "0" }).ok && (bodyOfForm({ ...f, emptyOn: false, empty: "0" }) as { body: ExportBody }).body.empty).toBeUndefined();
    const asked = bodyOfForm({ ...f, emptyOn: true, empty: "−99,5" });
    expect(asked.ok && asked.body.empty).toBe(-99.5);
    const zero = bodyOfForm({ ...f, emptyOn: true, empty: "0" });
    expect(zero.ok && zero.body.empty).toBe(0); // the user's own choice
  });

  it("the file belongs to its format: another format — the file is chosen again", () => {
    const f = formOf("dat");
    expect(chosenPath(f)).toBe(f.path);
    expect(chosenPath(nextFormat(f, "csv"))).toBeNull();
    expect(nextFormat({ ...f, encoding: "bocu1" }, "csv").encoding).toBe(""); // not an encoding of a CSV
    expect(nextFormat({ ...f, encoding: "cp1251" }, "csv").encoding).toBe("cp1251");
    expect(nextFormat({ ...f, encoding: "utf-8" }, "csv").encoding).toBe("utf-8");
  });

  it("names: the file of a model with the extension of the format, no characters a name cannot have", () => {
    expect(defaultFileName("Руда", "dat")).toBe("Руда.dat");
    expect(defaultFileName("А/Б: «В»?", "parquet")).toBe("А_Б_ «В»_.parquet");
    expect(defaultFileName(null, "omf")).toBe("блочная-модель.omf");
    expect(defaultFileName("...", "csv")).toBe("блочная-модель.csv");
    expect(FORMATS.map((f) => f.ext)).toEqual([".dat", ".csv", ".parquet", ".omf"]);
    expect(formatOfPath("D:\\Выгрузка\\модель.PARQUET")).toBe("parquet");
    expect(formatOfPath("/a/b.txt")).toBe("csv");
    expect(formatOfPath("/a/b.gpx")).toBeUndefined();
  });
});

describe("what is wrong is told before the core is asked", () => {
  const bad = (f: Partial<ExportForm>, names: string[] | null = null) => {
    const r = bodyOfForm(form({ path: "/a/b.dat", pathFormat: "dat", ...f }), names);
    if (r.ok) throw new Error("accepted");
    expect(r.issue.message).toMatch(/[А-Яа-яЁё]/);
    expect(r.issue.hint).not.toBe("");
    return r.issue.field;
  };

  it("no model, no file, no field, an empty list of fields", () => {
    expect(bodyOfForm({ ...EMPTY_FORM }).ok).toBe(false);
    expect(bad({ modelId: null })).toBe("model");
    expect(bad({ path: null })).toBe("path");
    expect(bad({ allFields: false, fields: [] })).toBe("fields");
    expect(bad({ allFields: false, fields: ["Нет такого"] }, ["AU"])).toBe("fields");
  });

  it("OMF with no field at all, and a model with no fields", () => {
    expect(bad({ format: "omf", pathFormat: "omf", allFields: false, fields: [] })).toBe("fields");
    expect(bad({ format: "omf", pathFormat: "omf" }, [])).toBe("fields");
    const dat = bodyOfForm(form({ path: "/a/b.dat", pathFormat: "dat" }), []);
    expect(dat.ok).toBe(true); // a DAT of a model with no fields has the centres and the sizes
  });

  it("a code of «пусто» that is no number; a decimal mark equal to the delimiter; a wrong restrictor", () => {
    expect(bad({ emptyOn: true, empty: "abc" })).toBe("empty");
    expect(bad({ emptyOn: true, empty: "" })).toBe("empty"); // not set is not 0
    expect(bad({ format: "csv", pathFormat: "csv", delimiter: ",", decimal: "," })).toBe("decimal");
    expect(bad({ restrictOn: true })).toBe("restrict"); // an empty group has no rules
  });

  it("the arguments of the command: every wrong one in its place, in Russian", () => {
    const badArgs = (a: Record<string, unknown>) => {
      const r = bodyOfArgs({ model_id: 1, path: "/a/b.dat", ...a });
      if (r.ok) throw new Error("accepted");
      expect(r.issue.message).toMatch(/[А-Яа-яЁё]/);
      expect(r.issue.hint).not.toBe("");
      return r.issue.field;
    };
    expect(bodyOfArgs({}).ok).toBe(false);
    expect(badArgs({ model_id: 1.5 })).toBe("model");
    expect(badArgs({ model_id: undefined })).toBe("model");
    expect(badArgs({ path: "" })).toBe("path");
    expect(badArgs({ format: "gpx" })).toBe("format");
    expect(badArgs({ fields: 5 })).toBe("fields");
    expect(badArgs({ filter_id: 0 })).toBe("filter");
    expect(badArgs({ restrict: "{не json" })).toBe("restrict");
    expect(badArgs({ restrict: "[1]" })).toBe("restrict");
    expect(badArgs({ header: "x" })).toBe("header");
    expect(badArgs({ encoding: "ebcdic" })).toBe("encoding");
    expect(badArgs({ format: "dat", encoding: "utf-16" })).toBe("encoding"); // a CSV's encoding, not a DAT's
    expect(badArgs({ delimiter: "x" })).toBe("delimiter");
    expect(badArgs({ decimal: "ё" })).toBe("decimal");
    expect(badArgs({ empty: "abc" })).toBe("empty");
    expect(badArgs({ format: "csv", delimiter: ",", decimal: "," })).toBe("decimal");
  });

  it("the format of the call: by the name when not given; «tab» is a tab; the fields as a list or a text", () => {
    const r = bodyOfArgs({ model_id: 1, path: "/a/b.csv", delimiter: "tab", fields: "AU, DOM", empty: -99, overwrite: true });
    expect(r.ok && r.body).toEqual({ format: "csv", path: "/a/b.csv", fields: ["AU", "DOM"], delimiter: "\t", empty: -99, overwrite: true });
    const list = bodyOfArgs({ model_id: 1, path: "/a/b.parquet", fields: ["AU"] });
    expect(list.ok && list.body).toEqual({ format: "parquet", path: "/a/b.parquet", fields: ["AU"] });
    const noext = bodyOfArgs({ model_id: 1, path: "/a/модель" });
    expect(noext.ok && noext.body.format).toBe("dat");
  });
});

describe("the refusals of the core are shown in their place of the form", () => {
  const field = (name: string, f: Partial<ExportForm> = {}) => {
    const body = REFUSED[name].answer.body as { code: string; param?: string };
    return fieldOfRefusal(body.code, body.param ?? null, form(f));
  };

  it("every refusal of the request has words, a hint and a place", () => {
    for (const [name, run] of Object.entries(REFUSED)) {
      if (run.answer.status === 200) continue; // `overwrite`: the core took it
      const b = run.answer.body as { code: string; message: string; hint: string };
      expect(b.message, name).toMatch(/[А-Яа-яЁё]/);
      expect(b.hint, name).not.toBe("");
      expect([404, 409, 422, 200]).toContain(run.answer.status);
    }
  });

  it("the place of each", () => {
    expect(field("files_exist")).toBe("path");
    expect(REFUSED.files_exist.answer.status).toBe(409);
    expect((REFUSED.files_exist.answer.body as { files: string[] }).files).toEqual(["model.dat"]);
    expect(field("omf_subblocks")).toBe("format");
    expect(field("omf_no_fields")).toBe("fields");
    expect(field("inside_project")).toBe("path");
    expect(field("relative")).toBe("path");
    expect(field("no_folder")).toBe("path");
    expect(field("no_path")).toBe("path");
    expect(field("format")).toBe("format");
    expect(field("encoding")).toBe("encoding");
    expect(field("delimiter")).toBe("delimiter");
    expect(field("decimal_is_delimiter")).toBe("decimal");
    expect(field("empty_text")).toBe("empty");
    expect(field("field_missing")).toBe("fields");
    expect(field("filter_missing")).toBe("filter");
    expect(field("restrict_field", { restrictOn: true })).toBe("restrict");
    expect(field("restrict_field", { restrictOn: true, filterId: 3 })).toBe("filter");
    expect(field("model_missing")).toBe("model");
    expect(field("not_object")).toBe("general");
  });

  it("the overwrite is the user's: the same request with `overwrite` writes", () => {
    expect(REFUSED.overwrite.request.overwrite).toBe(true);
    expect(REFUSED.overwrite.job?.status).toBe("done");
  });

  it("a model with sub-blocks is not offered OMF: the reason is the words of the core", () => {
    const why = omfReason(true, "sub")!;
    expect(why).toContain("с субблоками");
    expect(why).toContain("только после регуляризации");
    expect((REFUSED.omf_subblocks.answer.body as { message: string }).message).toContain("только после регуляризации");
    expect(omfReason(false, "m")).toBeNull();
    expect(omfReason(undefined, "m")).toBeNull();
  });

  it("the failures of the job: the code, the words and the place; nothing is written", () => {
    const codes = Object.fromEntries(Object.entries(FAILED).map(([k, v]) => [k, v.job!.error!.code]));
    expect(codes).toEqual({ encoding_char: "export_encoding", empty_code_taken: "empty_code_taken", nothing_passes: "export_empty" });
    for (const run of Object.values(FAILED)) {
      expect(run.job!.status).toBe("failed");
      expect(run.job!.result).toBeNull();
      expect(run.job!.error!.message).toMatch(/[А-Яа-яЁё]/);
      expect(run.job!.error!.hint).not.toBe("");
    }
    const place = (name: string, f: Partial<ExportForm> = {}) => {
      const e = FAILED[name].job!.error!;
      return issueOfRefusal({ ...e, param: null }, form(f)).field;
    };
    expect(place("encoding_char")).toBe("encoding");
    expect(place("empty_code_taken")).toBe("empty");
    expect(place("nothing_passes", { restrictOn: true })).toBe("restrict");
    expect(place("nothing_passes", { filterId: 3 })).toBe("filter");
    expect(place("nothing_passes")).toBe("general");
  });
});

describe("the file written, as the core tells it", () => {
  it("the result of each job is read: the path, the format, the blocks, the bytes", () => {
    for (const [name, run] of Object.entries(RUNS)) {
      const r = readResult(run.job!.result);
      expect(r, name).not.toBeNull();
      expect(r!.format).toBe(run.request.format);
      if (name !== "dat_filtered") expect(r!.rows).toBe(r!.total); // only the filter leaves blocks out
      expect(r!.warnings).toEqual((run.job!.result as { warnings: string[] }).warnings);
    }
    expect(readResult(RUNS.dat.job!.result)).toMatchObject({ rows: 350, total: 350, bytes: 32301 });
    expect(readResult(RUNS.omf.job!.result)!.bytes).toBeNull(); // the core does not give the size of an OMF: not guessed, not 0
    expect(readResult(null)).toBeNull();
    expect(readResult({ path: "/a" })).toBeNull();
    expect(readResult({ path: "/a", rows: 1, total: 1, format: "gpx" })).toBeNull();
  });

  it("the blocks that did not pass the filter are told: written of total", () => {
    const filtered = readResult(RUNS.dat_filtered.job!.result)!;
    expect(filtered.rows).toBeLessThan(filtered.total);
    expect(ws(selectionText(filtered)!)).toMatch(/^Записано \d+ из 350 блоков: остальные не прошли фильтр или ограничитель\.$/);
    expect(selectionText(readResult(RUNS.dat.job!.result)!)).toBeNull();
    expect(ws(resultText("Повёрнутая", filtered))).toContain("блоков " + String(filtered.rows) + " из 350 (остальные не прошли отбор)");
  });

  it("the words about the file: the model, the format, the blocks, the size, the path", () => {
    expect(ws(resultText("Повёрнутая", readResult(RUNS.dat.job!.result)!))).toBe("Модель «Повёрнутая» записана в Micromine DAT: блоков 350 (31,5 КБ) — /выгрузка/model.dat");
    expect(resultText("Повёрнутая", readResult(RUNS.omf.job!.result)!)).toBe("Модель «Повёрнутая» записана в OMF v1 (Leapfrog OMF 0.9): блоков 350 — /выгрузка/model.omf");
    expect([sizeText(12), sizeText(2048), sizeText(839_680), sizeText(3.4 * 1024 ** 2), sizeText(1.25 * 1024 ** 3)].map(ws)).toEqual(["12 Б", "2 КБ", "820 КБ", "3,4 МБ", "1,3 ГБ"]);
  });

  it("the formats the window offers are the formats of the core; Datamine DM is not one", () => {
    expect(FORMATS.map((f) => f.id)).toEqual(["dat", "csv", "parquet", "omf"] satisfies Format[]);
    expect(Object.values(RUNS).map((r) => r.request.format).sort()).toEqual(["csv", "csv", "csv", "csv", "dat", "dat", "dat", "dat", "omf", "parquet", "parquet"]);
  });
});

describe("«Свойства» of a model: the turn, the sub-blocks, the checks", () => {
  const model = A.model.body as BlockModelInfo;
  const sub = A.sub_model.body as BlockModelInfo;
  const list = (A.blocks.body as { block_models: BlockModelInfo[] }).block_models;

  it("a turned regular model: the angle about Z; no sub-blocks", () => {
    const facts = gridFacts(model);
    expect(facts.find((f) => f.label === "Поворот")?.value).toBe("30° вокруг Z, против часовой стрелки при виде сверху");
    expect(facts.map((f) => f.label)).toContain("Угол модели");
    expect(facts.some((f) => f.label === "Субблоки")).toBe(false);
    expect(subFacts(model)).toEqual([]);
  });

  it("a model with sub-blocks: the division, the levels, the blocks in the parents; the turn is absent when there is none", () => {
    const facts = gridFacts(sub);
    expect(facts.some((f) => f.label === "Поворот")).toBe(false);
    expect(ws(facts.find((f) => f.label === "Субблоки")!.value)).toBe("родитель делится на 8 × 4 × 4, уровней 3 × 2 × 2");
    expect(ws(facts.find((f) => f.label === "Блоков и родителей")!.value)).toBe("170 блоков в 19 родителях");
    expect(v2(sub).sub).toBe(true);
    expect(v2(list.find((m) => m.id === IDS.sub)!).sub).toBe(true); // the list tells it too: OMF is not offered
  });

  it("the checks of a clean import: no finding, the volume", () => {
    const rows = checkRows(v2(sub).checks)!;
    expect(rows.map((r) => [r.label, r.found])).toEqual([["Дубликаты", false], ["Перекрытия", false], ["Пустые поля", false], ["Постоянные поля", false], ["Объём блоков", false]]);
    expect(rows[1].value).toBe("нет (точно, на сетке наименьшего блока)");
    expect(ws(rows[4].value)).toBe("4 864 м³");
  });

  it("a model without checks (made before them) is not «0 findings»", () => {
    expect(checkRows(null)).toBeNull();
    expect(checkRows(undefined)).toBeNull();
  });

  it("the findings of a file with repeated rows and overlaps (the answers of the import)", () => {
    const imp = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/import_window_answers.json"), "utf-8"));
    const dups = checkRows(imp.models.dups.body.checks)!;
    expect(dups[0]).toEqual({ label: "Дубликаты", value: "2 — остаётся первая строка", found: true });
    expect(imp.models.dups.body.checks.examples.map(exampleText)).toEqual(["строка 104 — повтор строки 3", "строка 105 — повтор строки 5"]);
    const over = checkRows(imp.models.over.body.checks)!;
    expect(over[1].found).toBe(true);
    expect(over[1].value).toContain("строки сохранены");
    expect(imp.models.over.body.checks.examples.some((e: { check: string }) => e.check === "overlap")).toBe(true);
    const constant = checkRows(imp.models.dat.body.checks)!;
    expect(constant.find((r) => r.label === "Постоянные поля")?.found).toBe(false);
  });
});
