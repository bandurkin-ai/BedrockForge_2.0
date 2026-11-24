// The step «Блочная модель» of «Импорт файлов» (ТЗ 7.31, 8.7.2; ADR 0023 решения 5–7; Э17 17.38) on the REAL answers of
// the core (`core/tests/data/blocks/import_window_answers.json`, made by `core/tests/test_blocks_import_window_answers.py`
// through `POST /import/objects/inspect`, `POST /import/objects` and `GET /blocks/{id}`): the answers of the wizard are
// the bodies the core got (roles, units, empty codes, the grid with the turn, the axes of the centres), the columns
// read as the core found them, the check of three centres, the refusals with their rows, the findings of the checks.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  blocksWire,
  check3Text,
  choicesOf,
  draftErrors,
  emptyDraft,
  findingsText,
  foundChoice,
  gridText,
  hasErrors,
  headline,
  loadedFields,
  parseCodes,
  parseRotation,
  patchDraft,
  rowsText,
  shownChoice,
  turnNote,
  type BlocksDraft,
  type BlocksInfo,
} from "../src/modules/blocks/import/model";
import { blocksErrors, canStartObjects, fileCounts, initialObjects, startBody, totalsText, type Composition, type FileOk, type ObjectsState } from "../src/modules/importing/objects";
import { routeImport } from "../src/modules/importing/route";

const A = JSON.parse(readFileSync(resolve(__dirname, "../../core/tests/data/blocks/import_window_answers.json"), "utf-8"));
const ROOT = "/данные";
/** The numbers are grouped with a no-break space, as the interface prints them. */
const ws = (text: string): string => text.replace(/\u00a0/g, " ");

type Case = { request: { files: string[]; options: Record<string, { blocks?: Record<string, unknown>; name?: string }> }; answer: { status: number; body: Composition } };
const INSPECT = A.inspect as Record<string, Case>;
const composition = (name: string): Composition => INSPECT[name].answer.body;
const entry = (name: string, i = 0): FileOk => composition(name).files[i] as FileOk;
const blocksOf = (name: string): BlocksInfo => entry(name).blocks as BlocksInfo;
const optionsOf = (name: string) => Object.values(INSPECT[name].request.options)[0];

describe("the columns as the core found them", () => {
  it("a Micromine DAT: the centre and the size by their names, the fields with their roles and units", () => {
    const info = blocksOf("dat");
    expect(entry("dat").kind).toBe("block_model");
    expect(info.format).toBe("dat");
    expect(info.columns.map((c) => [c.name, c.role])).toEqual([["EAST", "x"], ["NORTH", "y"], ["RL", "z"], ["_EAST", "dx"], ["_NORTH", "dy"], ["_RL", "dz"], ["AU", null], ["ROCK", null]]);
    expect(info.columns.map((c) => foundChoice(info, c.name))).toEqual(["x", "y", "z", "dx", "dy", "dz", "grade", "code"]);
    expect(info.fields.map((f) => [f.name, f.kind, f.role, f.unit])).toEqual([["AU", "number", "grade", "г/т"], ["ROCK", "category", "code", null]]);
    expect(info.empty_candidates).toEqual([-99]); // offered, never applied by itself
    expect(info.fields[0].empty).toEqual([-99]);
  });

  it("a column the core leaves out is «Пропустить», a constant field is told as one", () => {
    const real = blocksOf("real.dm");
    expect(real.fields.find((f) => f.name === "1 Filter")?.constant).toBe(true);
    expect(foundChoice(real, "1 - Insi")).toBe("field"); // a field with no role the core can tell
    const none = { ...real, columns: [...real.columns, { name: "IJK", type: "number" as const, role: null }] };
    expect(foundChoice(none, "IJK")).toBe("skip");
  });

  it("the user's choice wins over the found one; «Как определено» is not a choice to show", () => {
    const info = blocksOf("dat");
    const draft = patchDraft(null, { columns: { AU: "density", ROCK: "auto" } });
    expect(shownChoice(info, draft, "AU")).toBe("density");
    expect(shownChoice(info, draft, "ROCK")).toBe("code");
    expect(shownChoice(info, null, "EAST")).toBe("x");
  });

  it("a text column is never a centre, a size, a grade, a density or a FRAC", () => {
    const ids = choicesOf({ name: "ROCK", type: "text", role: null }).map((c) => c.id);
    expect(ids).toEqual(["auto", "field", "code", "class", "skip"]);
    expect(choicesOf({ name: "AU", type: "number", role: null }).map((c) => c.id)).toEqual(["auto", "x", "y", "z", "dx", "dy", "dz", "field", "grade", "density", "frac", "code", "class", "skip"]);
  });

  it("the headline: the format, the blocks, the parents, the sub-blocks", () => {
    expect(ws(headline(blocksOf("dat")))).toBe("Micromine DAT · блоков 103 · родителей 103");
    expect(ws(headline(blocksOf("sub_dm")))).toBe("Datamine DM · блоков 170 · родителей 19 · с субблоками");
    expect(ws(headline(blocksOf("real.fbm")))).toBe("Datamine FBM · блоков 169 070 · родителей 169 070");
  });

  it("the totals and the counts of a file speak of models and blocks, not of layers", () => {
    expect(fileCounts(entry("dat"))).toBe("блочных моделей: 1 · блоков: 103");
    expect(totalsText(composition("all").totals)).toContain("блочных моделей: 2");
  });
});

describe("the answers of the wizard are the body the core got", () => {

  it("roles of fields, units, the code of «пусто» and the name: the recorded request of `dat_asked`", () => {
    const info = blocksOf("dat");
    const draft: BlocksDraft = { ...emptyDraft(), columns: { AU: "grade", ROCK: "class" }, units: { AU: "%" }, empty: "-99" };
    expect(blocksWire(draft, info)).toEqual(optionsOf("dat_asked").blocks);
    expect(optionsOf("dat_asked").name).toBe("Моя модель");
  });

  it("«Пропустить» is a skip of the field, a unit of a skipped field is not sent", () => {
    const info = blocksOf("dat");
    const draft: BlocksDraft = { ...emptyDraft(), columns: { ROCK: "skip" }, units: { ROCK: "т" } };
    expect(blocksWire(draft, info)).toEqual(optionsOf("dat_skipped").blocks);
  });

  it("the grid with the turn: the recorded request of `turned_csv`, a comma and a point alike", () => {
    const g = A.grid as { corner: number[]; rotation: number; size: number[] };
    const text = (v: number, comma: boolean) => (comma ? String(v).replace(".", ",") : String(v));
    for (const comma of [false, true]) {
      const draft: BlocksDraft = {
        ...emptyDraft(),
        corner: g.corner.map((v) => text(v, comma)) as [string, string, string],
        size: g.size.map((v) => text(v, comma)) as [string, string, string],
        rotation: text(g.rotation, comma),
      };
      expect(blocksWire(draft, blocksOf("turned_csv"))).toEqual(optionsOf("turned_csv").blocks);
    }
  });

  it("the wrong angle of the recorded request is a valid number: the check of three centres refuses it", () => {
    expect(optionsOf("turned_wrong_angle").blocks).toEqual({ grid: { corner: [466167.5, 4584527.5, 202.5], size: [8, 8, 4], rotation: 35 } });
    const info = blocksOf("turned_wrong_angle");
    expect(info.error?.code).toBe("blocks_unaligned");
    expect(info.check3).toBeNull();
  });

  it("the roles of the centres chosen by the user: the recorded request of `roles_chosen`", () => {
    const info = blocksOf("no_roles");
    const draft: BlocksDraft = { ...emptyDraft(), columns: { A: "x", B: "y", C: "z" } };
    expect(blocksWire(draft, info)).toEqual(optionsOf("roles_chosen").blocks);
    expect(blocksOf("roles_chosen").error).toBeNull();
    expect(blocksOf("roles_chosen").fields.map((f) => f.name)).toEqual(["D"]);
  });

  it("«Загрузить как блочную модель»: an empty answer `{}`, the columns found by the core", () => {
    expect(blocksWire(emptyDraft(), null)).toEqual({});
    expect(optionsOf("no_roles").blocks).toEqual({});
  });

  it("a centre or a size given to another column releases the column that had the role", () => {
    const info = blocksOf("dat");
    const wire = blocksWire({ ...emptyDraft(), columns: { EAST: "field", _EAST: "skip", _NORTH: "grade" } }, info);
    expect(wire.roles).toEqual({ x: "", dx: "", dy: "" });
    expect(wire.fields).toEqual({ _EAST: { skip: true }, _NORTH: { role: "grade" } });
    const moved = blocksWire({ ...emptyDraft(), columns: { AU: "x" } }, info);
    expect(moved.roles).toEqual({ x: "AU" });
  });

  it("the axes of the centres of a turned model and the delimiter of a text file are sent only when chosen", () => {
    expect(blocksWire(emptyDraft(), null)).toEqual({});
    expect(blocksWire({ ...emptyDraft(), centres: "model", delimiter: "\t" }, null)).toEqual({ centres: "model", delimiter: "\t" });
  });

  it("the sent body of the files: `name` and `blocks` by the path, the others as before", () => {
    const comp = composition("dat_asked");
    const path = Object.keys(INSPECT.dat_asked.request.options)[0];
    const s: ObjectsState = { ...initialObjects(), paths: [path], composition: comp, options: { [path]: { name: "Моя модель", blocks: { ...emptyDraft(), columns: { AU: "grade", ROCK: "class" }, units: { AU: "%" }, empty: "-99" } } } };
    const body = startBody(s);
    expect(body.options[path]).toEqual({ name: "Моя модель", blocks: optionsOf("dat_asked").blocks });
    expect(body.files).toEqual([path]);
  });
});

describe("what is typed wrong is told before anything is sent", () => {
  it("a triple is all three or none; texts that are not numbers are told; counts and division are whole", () => {
    expect(hasErrors(draftErrors(emptyDraft()))).toBe(false);
    expect(draftErrors(patchDraft(null, { corner: ["1", "", ""] })).corner).toContain("все три значения");
    expect(draftErrors(patchDraft(null, { size: ["a", "1", "1"] })).size).toContain("«a» — не число");
    expect(draftErrors(patchDraft(null, { size: ["0", "1", "1"] })).size).toContain("больше нуля");
    expect(draftErrors(patchDraft(null, { counts: ["2,5", "1", "1"] })).counts).toContain("целые числа");
    expect(draftErrors(patchDraft(null, { div: ["256", "1", "1"] })).div).toContain("не больше 128");
    expect(draftErrors(patchDraft(null, { corner: ["1 000,5", "2", "-3"] })).corner).toBeUndefined();
  });

  it("the angle: a number from −180 (not included) to 180", () => {
    expect(parseRotation("30,5")).toEqual({ value: 30.5, error: null });
    expect(parseRotation("")).toEqual({ value: null, error: null }); // not set: not 0
    expect(parseRotation("−45").value).toBe(-45);
    expect(parseRotation("180").value).toBe(180);
    expect(parseRotation("-180").error).toContain("от −180°");
    expect(parseRotation("200").error).toContain("от −180°");
    expect(parseRotation("a").error).toContain("не число");
  });

  it("the codes of «пусто»: numbers through «;», a comma is a decimal point; text is told", () => {
    expect(parseCodes("-99; -999,5")).toEqual({ codes: [-99, -999.5], error: null });
    expect(parseCodes("")).toEqual({ codes: [], error: null });
    expect(parseCodes("-99; abc").error).toContain("«abc» — не число");
    expect(blocksWire(patchDraft(null, { empty: "-99; abc" }), null).empty).toBeUndefined(); // the wrong text is not sent
  });

  it("a wrong draft stops the start, a right one does not", () => {
    const path = Object.keys(INSPECT.dat.request.options)[0] ?? `${ROOT}/model.dat`;
    const base: ObjectsState = { ...initialObjects(), paths: [path], composition: composition("dat") };
    const files = composition("dat").files.map((f) => ({ ...f, path }));
    const s: ObjectsState = { ...base, composition: { ...composition("dat"), files }, options: {} };
    expect(canStartObjects(s)).toBe(true);
    const bad: ObjectsState = { ...s, options: { [path]: { blocks: patchDraft(null, { rotation: "abc" }) } } };
    expect(canStartObjects(bad)).toBe(false);
    expect(Object.values(blocksErrors(bad))[0]).toContain("не число");
    const good: ObjectsState = { ...s, options: { [path]: { blocks: patchDraft(null, { rotation: "30" }) } } };
    expect(canStartObjects(good)).toBe(true);
  });
});

describe("the grid, the check of three centres, the turn", () => {
  it("the grid in words: counts, parent size, the corner, the turn and the division", () => {
    expect(ws(gridText(blocksOf("dat").grid!))).toBe("6 × 5 × 4 родителей по 10 × 20 × 5 м; начало 1 000,5; 2 000,25; −50");
    expect(ws(gridText(blocksOf("turned_csv").grid!))).toBe("4 × 3 × 2 родителей по 8 × 8 × 4 м; начало 466 167,5; 4 584 527,5; 202,5; поворот 30°; деление на субблоки 8 × 4 × 4");
  });

  it("three centres of the file against the grid: the rows from 1 and the distance", () => {
    expect(check3Text(blocksOf("dat"))).toEqual({ ok: true, text: "Три центра файла (строки 1, 52, 103) совпали с сеткой: расхождение 0 м." });
    const turned = check3Text(blocksOf("turned_csv"))!;
    expect(turned.ok).toBe(true);
    expect(turned.text).toContain("строки 1, 86, 170");
    expect(turned.text).toContain("расхождение 2,4·10⁻¹⁰ м");
    expect(check3Text(blocksOf("off_grid"))).toBeNull(); // a refusal has no three centres
    const failed = check3Text({ ...blocksOf("dat"), check3: { ok: false, dist: 3.5, rows: [0, 51, 102] } })!;
    expect(failed.ok).toBe(false);
    expect(failed.text).toContain("не совпали с сеткой: расхождение 3,5");
  });

  it("a turned model is read «предварительно» (№ 64), an unturned one has no such note; a typed angle counts", () => {
    expect(turnNote(blocksOf("dat"), null)).toBeNull();
    expect(turnNote(blocksOf("turned_csv"), null)).toContain("Micromine");
    expect(turnNote(blocksOf("turned_csv"), null)).toContain("предварительно");
    expect(turnNote(blocksOf("sub_dm"), patchDraft(null, { rotation: "30" }))).toContain("Datamine (ANGLE1 вокруг оси 3)");
    expect(turnNote(blocksOf("sub_dm"), patchDraft(null, { rotation: "0" }))).toBeNull();
  });

  it("the Datamine and the Micromine files give the same model: the grid and the check", () => {
    expect(blocksOf("sub_dm").grid).toEqual(blocksOf("sub_fbm").grid);
    expect(blocksOf("sub_dm").check3?.ok && blocksOf("sub_fbm").check3?.ok).toBe(true);
    expect(blocksOf("sub_dm").sub && blocksOf("sub_fbm").sub).toBe(true);
    // the owner's model (169 070 blocks): the same blocks in the `.dm` and in the `.fbm`; the grid of the `.fbm` is its own
    expect(blocksOf("real.dm").rows).toBe(blocksOf("real.fbm").rows);
    expect(blocksOf("real.dm").parents).toBe(blocksOf("real.fbm").parents);
    expect(blocksOf("real.dm").grid?.size).toEqual(blocksOf("real.fbm").grid?.size);
  });
});

describe("the refusals of the core are shown with their rows and their hint", () => {
  it("blocks off the grid: how many and the first rows from 1", () => {
    const err = blocksOf("off_grid").error!;
    expect(err.code).toBe("blocks_unaligned");
    expect(err.count).toBe(103);
    expect(rowsText(err)).toBe("Строки данных (с 1): 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 и другие");
    expect(err.message).toContain("за началом или за краем сетки");
    expect(err.hint).toContain("Проверьте начало и размер родителя");
    expect(rowsText(null)).toBe("");
  });

  it("a table without a centre: the refusal in Russian; a file the core did not take for blocks may be forced", () => {
    const err = blocksOf("no_roles").error!;
    expect(err.code).toBe("blocks_columns");
    expect(err.message).toBe("Не найдены столбцы центра блока: X, Y, Z.");
    expect(err.hint).toContain("в мастере импорта");
    const lone = composition("not_detected").files[0];
    expect(lone.ok).toBe(false);
    expect(!lone.ok && lone.error.code).toBe("no_coordinates");
  });

  it("a model the core cannot build does not start the import by itself; one that can does", () => {
    const only = (name: string): ObjectsState => ({ ...initialObjects(), paths: composition(name).files.map((f) => f.path), composition: composition(name) });
    expect(canStartObjects(only("off_grid"))).toBe(false);
    expect(canStartObjects(only("no_roles"))).toBe(false);
    expect(canStartObjects(only("dat"))).toBe(true);
    expect(canStartObjects(only("all"))).toBe(true); // the other files load
  });

  it("a wrong answer of the wizard is a refusal of the core in Russian (422 `blocks_options`)", () => {
    const answer = INSPECT.bad_option.answer as unknown as { status: number; body: { code: string; message: string; hint: string } };
    expect(answer.status).toBe(422);
    expect(answer.body).toMatchObject({ code: "blocks_options", message: "Оси центров «world» не подходят.", hint: "Выберите оси проекта или модели." });
  });
});

describe("the findings of the import", () => {
  it("the job tells duplicates and overlaps of the file from the checks of the entry", () => {
    const dups = A.jobs.dups.job.result.files[0];
    expect(dups).toMatchObject({ kind: "block_models", rows: 103, parents: 103, sub: false, checks: { duplicates: 2, overlaps: 0 } });
    expect(findingsText(dups.checks)).toEqual(["Повторяющиеся блоки: 2 — оставлена первая строка."]);
    const over = A.jobs.over.job.result.files[0];
    expect(over.sub).toBe(true);
    expect(findingsText(over.checks)).toEqual(["Перекрывающиеся блоки: 3 — строки сохранены, смотрите «Свойства» модели."]);
    expect(findingsText({ duplicates: 0, overlaps: 0 })).toEqual([]);
  });

  it("the file that did not load and the points beside it: the job result keeps both", () => {
    const r = A.jobs.failed_and_points.job.result;
    expect(r.failed.map((f: { file: string; code: string }) => [f.file, f.code])).toEqual([["noroles.csv", "no_coordinates"]]);
    expect(r.files.map((f: { file: string; kind: string }) => [f.file, f.kind])).toEqual([["off.csv", "block_models"], ["points.csv", "points"]]);
  });

  it("the journal of the job holds the finding in Russian", () => {
    const text = A.jobs.dups.log.lines.map((l: { text: string }) => l.text).join("\n");
    expect(text).toContain("Повторяющиеся блоки: 2. Оставлена первая строка.");
  });
});

describe("fields the model will have", () => {
  it("the skipped ones do not load", () => {
    const info = blocksOf("dat");
    expect(loadedFields(info, null).map((f) => f.name)).toEqual(["AU", "ROCK"]);
    expect(loadedFields(info, patchDraft(null, { columns: { ROCK: "skip" } })).map((f) => f.name)).toEqual(["AU"]);
  });
});

describe("where the files go", () => {
  it("«.fbm» and a block «.dm» go to the composition of objects, not to the wizard of drillholes", () => {
    const route = routeImport(["/a/model.fbm", "/a/model.dm", "/a/points.dat", "/a/table.csv"], "auto");
    expect(route.objects).toEqual(["/a/model.fbm", "/a/model.dm"]);
    expect(route.holes).toEqual(["/a/points.dat", "/a/table.csv"]);
    const forced = routeImport(["/a/model.dat", "/a/model.csv", "/a/model.fbm"], "objects");
    expect(forced.objects).toHaveLength(3);
    expect(forced.holes).toEqual([]);
  });
});
