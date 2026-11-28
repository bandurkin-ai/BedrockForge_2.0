// The refusal «Модель не ложится в сетку» (ADR 0030 решения 5–7, подшаг 27.4б; замечание А6): the panel of the choice
// by the sample of the answer 422 — the table of the axes, the candidates with the share of the blocks that lie, «Задать
// сетку вручную» fills the fields of the grid with the best candidate (the body the core gets has `grid.tol`),
// «Не импортировать» takes the file out of the selection; the numbers of the form (the division is any whole to 128, the
// tolerance is a number of metres).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { UnalignedChoice } from "../../src/modules/blocks/import/UnalignedChoice";
import {
  axisRows,
  blocksWire,
  check3Text,
  candidateGrid,
  candidateRows,
  draftErrors,
  emptyDraft,
  gridPatch,
  headline,
  listText,
  mismatchText,
  parseTol,
  patchDraft,
  percent,
  typed,
  unalignedOf,
  type BlocksError,
  type BlocksInfo,
} from "../../src/modules/blocks/import/model";
import type { FileOk, ObjectsWizard } from "../../src/modules/importing/objects";

/** The answer of the core to a model like the customer's (parent 10 × 10 × 10, the heights of the sub-blocks of any size). */
const A6: BlocksError = {
  code: "blocks_unaligned",
  message: "Блоков, которые нельзя уложить в сетку модели: 626 791 (не по подъячейкам деления родителя — 626 791). По Z 412 различных высот, общего деления родителя нет — субблоки произвольного размера.",
  hint: "Строки данных (с 1): 4, 9, 12 и другие. Задайте родителя и начало сетки вручную или отмените импорт файла.",
  count: 626791,
  rows: [3, 8, 11],
  by_reason: { off_grid: 0, crosses_parent: 0, off_subcells: 626791 },
  diag: {
    rows: 1012620,
    sample: 200000,
    columns: { x: "R 8", y: "R 8", z: "R 8", dx: "R 8", dy: "R 8", dz: "R 8" },
    causes: ["off_lattice"],
    text: ["По Z 412 различных высот, общего деления родителя нет — субблоки произвольного размера.", "По X общее деление родителя больше 128 (доли 1/7, 1/19)."],
    axes: [
      { axis: "X", parent: 10, origin: 0, sizes: [{ v: 10, n: 400000 }, { v: 2, n: 300000 }], distinct: 2, fractions: [{ v: 2, q: 5 }, { v: 10, q: 1 }], div: 5, residues: [{ v: 0, n: 700000 }], quantum: 1e-6, suggest_tol: null, fit: { total: 1012620, aligned: 1012620, before: 0, crosses: 0, off: 0 }, overflow: false },
      { axis: "Y", parent: 10, origin: 0, sizes: [{ v: 10, n: 400000 }, { v: 2, n: 300000 }], distinct: 2, fractions: [{ v: 2, q: 5 }], div: 5, residues: [{ v: 0, n: 700000 }], quantum: 1e-6, suggest_tol: null, fit: { total: 1012620, aligned: 1012620, before: 0, crosses: 0, off: 0 }, overflow: false },
      { axis: "Z", parent: 10, origin: 0, sizes: [{ v: 10, n: 385829 }, { v: 3.1415, n: 120 }], distinct: 412, fractions: [{ v: 3.1415, q: 0 }], div: null, residues: [{ v: 0, n: 385829 }, { v: 3.1415, n: 120 }], quantum: 1e-6, suggest_tol: null, fit: { total: 1012620, aligned: 385829, before: 0, crosses: 0, off: 626791 }, overflow: false },
    ],
    candidates: [
      { size: [10, 10, 10], corner: [23436265.92, 5000.5, 100], div: [5, 5, 1], tol: [1e-6, 1e-6, 1e-6], aligned: 0.381, crosses: 0, off_grid: 0, off_sub: 0.619, current: true },
      { size: [10, 10, 20], corner: [23436265.92, 5000.5, 100], div: [5, 5, 20], tol: [1e-6, 1e-6, 0.05], aligned: 0.62, crosses: 0, off_grid: 0, off_sub: 0.38, current: false },
    ],
  },
  choices: [
    { id: "grid", label: "Задать сетку вручную", grid: { corner: [23436265.92, 5000.5, 100], size: [10, 10, 20], div: [5, 5, 20], tol: 0.05, rotation: 0 }, aligned: 0.62 },
    { id: "list", label: "Импортировать списком блоков", note: "показ, фильтры, калькулятор, отчёт, экспорт; оценка и карьер — после регуляризации" },
    { id: "cancel", label: "Не импортировать этот файл" },
  ],
};

/** The refusal of a turned model: the core names the rows of the three centres and the distance, offers the same ways out. */
const TURNED: BlocksError = {
  code: "rotation_mismatch",
  message: "Центры блоков (строки 1, 6, 10) не совпадают с вычисленными: расстояние 0.37 м при допуске 1e-6 м.",
  hint: "Проверьте угол поворота вокруг Z, начало модели и оси, в которых записаны центры (проекта или модели), в мастере импорта.",
  rows: [0, 5, 9],
  dist: 0.37,
  choices: [
    { id: "grid", label: "Задать сетку вручную", grid: { corner: [100, 200, 50], size: [10, 10, 5], div: [2, 2, 2], tol: 1e-6, rotation: 20 }, aligned: null },
    { id: "list", label: "Импортировать списком блоков", note: "показ, фильтры, калькулятор, отчёт, экспорт; оценка и карьер — после регуляризации" },
    { id: "cancel", label: "Не импортировать этот файл" },
  ],
};

describe("the diagnosis of a refusal in the words of the window", () => {
  const un = unalignedOf(A6)!;

  it("is read from the refusal `blocks_unaligned` with a diagnosis, and from nothing else", () => {
    expect(un.grid?.id).toBe("grid");
    expect(un.cancel.label).toBe("Не импортировать этот файл");
    expect(un.lead).toContain("По Z 412 различных высот");
    expect(unalignedOf(null)).toBeNull();
    expect(unalignedOf({ ...A6, diag: undefined })).toBeNull();
    expect(unalignedOf({ ...A6, code: "no_column" })).toBeNull();
    expect(unalignedOf({ ...TURNED, choices: undefined })).toBeNull(); // a turn with no way out is a plain error
    expect(un.detailed).toBe(true);
    expect(un.list?.label).toBe("Импортировать списком блоков");
    expect(unalignedOf({ ...A6, choices: undefined })?.cancel.id).toBe("cancel"); // never without a way out
  });

  it("the table of the axes: the parent, the sizes with their counts, the fractions, the division, the starts", () => {
    const rows = axisRows(un.diag);
    expect(rows.map((r) => r.axis)).toEqual(["X", "Y", "Z"]);
    expect(rows[0]).toMatchObject({ parent: "10", div: "5", quantum: "0,000001", columns: "R 8 / R 8" });
    expect(rows[0].sizes).toBe("10 (400 000); 2 (300 000)");
    expect(rows[2].div).toBe("нет");
    expect(rows[2].sizes).toContain("всего различных 412");
    expect(rows[2].fractions).toBe("3,1415 — нет");
    expect(rows[2].residues).toBe("0 (385 829); 3,1415 (120)");
  });

  it("the candidates: the share of the blocks that lie on each, the current grid marked", () => {
    const rows = candidateRows(un.diag);
    expect(rows.map((r) => r.aligned)).toEqual(["38,1 %", "62 %"]);
    expect(rows[1]).toMatchObject({ size: "10 × 10 × 20", div: "5 × 5 × 20", tol: "0,05", current: false });
    expect(rows[0].current).toBe(true);
    expect([percent(1), percent(0.99999), percent(0), percent(0.0042)]).toEqual(["100 %", "99,9 %", "0 %", "0,42 %"]);
  });
});

describe("«Задать сетку вручную»: the fields of the grid and the body the core gets", () => {
  const un = unalignedOf(A6)!;
  const grid = un.grid!.grid!;

  it("fills the corner, the parent, the division, the tolerance and the turn — and not the counts", () => {
    const patch = gridPatch(grid);
    expect(patch).toEqual({
      corner: ["23436265,92", "5000,5", "100"],
      size: ["10", "10", "20"],
      counts: ["", "", ""],
      div: ["5", "5", "20"],
      rotation: "",
      tol: "0,05",
    });
    expect(typed(0.1 + 0.2)).toBe("0,3");
    expect(gridPatch({ ...grid, rotation: 30, tol: 1e-6 })).toMatchObject({ rotation: "30", tol: "" });
  });

  it("the typed grid goes to the core as numbers (a comma is a decimal point), with `tol`; nothing else is added", () => {
    const wire = blocksWire(patchDraft(emptyDraft(), gridPatch(grid)), null);
    expect(wire).toEqual({ grid: { corner: [23436265.92, 5000.5, 100], size: [10, 10, 20], div: [5, 5, 20], tol: 0.05 } });
    expect(blocksWire(emptyDraft(), null)).toEqual({});
  });

  it("a candidate of the table fills the form with its own grid (the biggest tolerance of its three)", () => {
    const g = candidateGrid(un.diag.candidates[1], 30);
    expect(g).toEqual({ corner: [23436265.92, 5000.5, 100], size: [10, 10, 20], div: [5, 5, 20], tol: 0.05, rotation: 30 });
  });

  it("the division is any whole number to 128; the tolerance — metres above zero", () => {
    expect(draftErrors(patchDraft(null, { div: ["5", "5", "3"] })).div).toBeUndefined();
    expect(draftErrors(patchDraft(null, { div: ["129", "1", "1"] })).div).toContain("не больше 128");
    expect(draftErrors(patchDraft(null, { div: ["2,5", "1", "1"] })).div).toContain("целые числа");
    expect(parseTol("0,005")).toEqual({ value: 0.005, error: null });
    expect(parseTol("")).toEqual({ value: null, error: null });
    expect(parseTol(undefined)).toEqual({ value: null, error: null });
    expect(parseTol("0").error).toContain("больше нуля");
    expect(parseTol("абв").error).toContain("не число");
    expect(draftErrors(patchDraft(null, { tol: "-1" })).tol).toContain("больше нуля");
  });
});

// ---- the panel -------------------------------------------------------------------------------------------------------

type Handler = { onClick?: () => void; disabled?: boolean };

/** The element with `data-testid` in the tree a component returned (the component has no hooks: it is a function). */
function find(node: ReactNode, testid: string): ReactElement<Handler & Record<string, unknown>> | null {
  if (!node || typeof node !== "object") return null;
  if (Array.isArray(node)) {
    for (const c of node) {
      const hit = find(c, testid);
      if (hit) return hit;
    }
    return null;
  }
  const el = node as ReactElement<Record<string, unknown>>;
  if (el.props?.["data-testid"] === testid) return el as ReactElement<Handler & Record<string, unknown>>;
  return find(el.props?.children as ReactNode, testid);
}

const info = { error: A6 } as unknown as BlocksInfo;
const file = { path: "/tmp/Геомет.DAT", file: "Геомет.DAT", ok: true, blocks: info } as unknown as FileOk;

function panel(options = {}) {
  const wizard = { setOptions: vi.fn(async () => undefined), remove: vi.fn() };
  const props = { wizard: wizard as unknown as ObjectsWizard, file, options, un: unalignedOf(A6)! };
  return { wizard, props, markup: renderToStaticMarkup(createElement(UnalignedChoice, props)), tree: UnalignedChoice(props) };
}

describe("the panel «Модель не ложится в сетку»", () => {
  it("shows the message, the rows from 1, the causes, the table of the axes and of the candidates, two buttons", () => {
    const { markup } = panel();
    const text = markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    expect(text).toContain("Модель не ложится в сетку");
    expect(text).toContain("626 791");
    expect(text).toContain("Строки данных (с 1): 4, 9, 12 и другие");
    expect(text).toContain("По X общее деление родителя больше 128");
    expect(text).toContain("Возможные причины: Размеры без общего деления (субблоки произвольного размера).");
    expect(markup).toContain('data-testid="unaligned-axes"');
    expect(markup).toContain('data-testid="unaligned-candidates"');
    expect(markup).toContain('role="alert"');
    expect(text).toContain("Задать сетку вручную");
    expect(text).toContain("Не импортировать этот файл");
    expect(text).toContain("определена по файлу");
    expect(markup).not.toContain("confirm(");
  });

  it("«Задать сетку вручную» reads the file again with the grid of the best candidate in the answers", () => {
    const { wizard, tree } = panel({ blocks: patchDraft(emptyDraft(), { columns: { AU: "grade" } }) });
    const button = find(tree, "unaligned-grid")!;
    expect(button.props.disabled).toBe(false);
    button.props.onClick!();
    expect(wizard.setOptions).toHaveBeenCalledTimes(1);
    const [path, patch] = wizard.setOptions.mock.calls[0] as unknown as [string, { blocks: { columns: Record<string, string>; size: string[]; div: string[]; tol: string } }];
    expect(path).toBe("/tmp/Геомет.DAT");
    expect(patch.blocks.size).toEqual(["10", "10", "20"]);
    expect(patch.blocks.div).toEqual(["5", "5", "20"]);
    expect(patch.blocks.tol).toBe("0,05");
    expect(patch.blocks.columns).toEqual({ AU: "grade" }); // what the user had said stays
  });

  it("a candidate of the table is taken by its button; the current grid has none", () => {
    const { wizard, tree, markup } = panel();
    expect(find(tree, "unaligned-take-0")).toBeNull();
    expect(markup).toContain('data-testid="unaligned-take-1"');
    find(tree, "unaligned-take-1")!.props.onClick!();
    expect((wizard.setOptions.mock.calls[0] as unknown as [string, { blocks: { size: string[] } }])[1].blocks.size).toEqual(["10", "10", "20"]);
  });

  it("«Не импортировать этот файл» takes the file out of the selection and nothing else", () => {
    const { wizard, tree } = panel();
    find(tree, "unaligned-cancel")!.props.onClick!();
    expect(wizard.remove).toHaveBeenCalledWith("/tmp/Геомет.DAT");
    expect(wizard.setOptions).not.toHaveBeenCalled();
  });

  it("with no grid to offer the button of the grid is off, the way out stays", () => {
    const bare: BlocksError = { ...A6, choices: [{ id: "grid", label: "Задать сетку вручную", grid: null, aligned: 0 }, A6.choices![2]], diag: { ...A6.diag!, candidates: [] } };
    const wizard = { setOptions: vi.fn(), remove: vi.fn() };
    const tree = UnalignedChoice({ wizard: wizard as unknown as ObjectsWizard, file, options: {}, un: unalignedOf(bare)! });
    expect(find(tree, "unaligned-grid")!.props.disabled).toBe(true);
    expect(find(tree, "unaligned-cancel")).not.toBeNull();
    expect(find(tree, "unaligned-candidates")).toBeNull();
  });
});

describe("the real answer of the core (the golden of the wizard)", () => {
  it("a refusal of a real model carries a diagnosis the window can show", () => {
    const answers = JSON.parse(readFileSync(resolve(__dirname, "../../../core/tests/data/blocks/import_window_answers.json"), "utf-8")) as {
      inspect: Record<string, { answer: { body: { files: { blocks?: BlocksInfo }[] } } }>;
    };
    const err = answers.inspect.off_grid.answer.body.files[0].blocks!.error!;
    const un = unalignedOf(err)!;
    expect(un).not.toBeNull();
    expect(un.diag.causes).toEqual(["crosses_parent", "off_grid"]);
    expect(candidateRows(un.diag)[0].aligned).toBe("100 %");
    expect(un.grid?.grid?.size).toEqual([10, 20, 5]);
    expect(JSON.stringify(err).length).toBeLessThan(16 * 1024);
    expect(axisRows(un.diag)[0].axis).toBe("X");
  });
});

describe("«Импортировать списком блоков» (ADR 0030 решение 14; требование А6)", () => {
  it("the panel offers it between the grid by hand and «Не импортировать»; it sets `blocks.layout` and keeps the answers", () => {
    const { wizard, tree, markup } = panel({ blocks: patchDraft(emptyDraft(), { columns: { AU: "grade" } }) });
    const text = markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    expect(text.indexOf("Задать сетку вручную")).toBeLessThan(text.indexOf("Импортировать списком блоков"));
    expect(text.indexOf("Импортировать списком блоков")).toBeLessThan(text.indexOf("Не импортировать этот файл"));
    find(tree, "unaligned-list")!.props.onClick!();
    expect(wizard.setOptions).toHaveBeenCalledTimes(1);
    const [path, patch] = wizard.setOptions.mock.calls[0] as unknown as [string, { blocks: { layout: string; columns: Record<string, string> } }];
    expect(path).toBe("/tmp/Геомет.DAT");
    expect(patch.blocks.layout).toBe("list");
    expect(patch.blocks.columns).toEqual({ AU: "grade" });
    expect(wizard.remove).not.toHaveBeenCalled();
  });

  it("without the choice from the core there is no button (the old core, nothing is invented)", () => {
    const old: BlocksError = { ...A6, choices: A6.choices!.filter((c) => c.id !== "list") };
    const wizard = { setOptions: vi.fn(), remove: vi.fn() };
    const tree = UnalignedChoice({ wizard: wizard as unknown as ObjectsWizard, file, options: {}, un: unalignedOf(old)! });
    expect(find(tree, "unaligned-list")).toBeNull();
  });

  it("the body of the answers: `layout: list` goes to the core, the grid fields stay as they were", () => {
    expect(blocksWire(patchDraft(emptyDraft(), { layout: "list" }), null)).toEqual({ layout: "list" });
    expect(blocksWire(patchDraft(emptyDraft(), { layout: "grid" }), null)).toEqual({});
    expect(blocksWire(patchDraft(emptyDraft(), { layout: "list", rotation: "30" }), null)).toEqual({ layout: "list", grid: { rotation: 30 } });
  });

  it("the summary «Список блоков: N, охват …» and the headline of a list; no check of three centres", () => {
    const listed = {
      format: "dat",
      label: "Micromine DAT",
      rows: 1012620,
      rows_exact: true,
      layout: "list",
      list: { rows: 1012620, extent: [1000, 2000, 100, 1200.5, 2400, 160], size_min: [2, 2, 0.5], size_max: [10, 10, 10], cells: [20, 40, 6], cell: [10, 10, 10], parents: 4800, whole: true },
      check3: { ok: true, dist: 0, rows: [0, 1, 2] },
      parents: 4800,
      sub: false,
      error: null,
    } as unknown as BlocksInfo;
    const plain = (t: string) => t.replace(/[\u00a0\u202f]/g, " "); // the thousands of ru-RU are a no-break space
    const text = plain(listText(listed)!);
    expect(text).toContain("Список блоков: 1 012 620");
    expect(text).toContain("охват X 1 000…1 200,5, Y 2 000…2 400, Z 100…160 м");
    expect(text).toContain("размеры блоков от 2 × 2 × 0,5 до 10 × 10 × 10 м");
    expect(text).toContain("охватывающая сетка 20 × 40 × 6 ячеек по 10 × 10 × 10 м, занято ячеек 4 800");
    expect(plain(headline(listed))).toBe("Micromine DAT · блоков 1 012 620 · список блоков");
    expect(check3Text(listed)).toBeNull();
    expect(listText({ ...listed, layout: "grid" } as BlocksInfo)).toBeNull();
    expect(listText({ ...listed, list: null } as BlocksInfo)).toBeNull();
  });
});

describe("the panel for the refusal of a turned model (`rotation_mismatch` with the choices, no diagnosis)", () => {
  const un = unalignedOf(TURNED)!;

  it("is read from the choices; there is no table, the cause is the rows and the distance of the error", () => {
    expect(un.detailed).toBe(false);
    expect(un.grid?.grid?.rotation).toBe(20);
    expect(un.list?.id).toBe("list");
    expect(un.lead).toBe(TURNED.message);
    expect(mismatchText(TURNED)).toBe("Центры блоков в строках 1, 6, 10 не совпали с вычисленными по сетке. Наибольшее расхождение: 0,37 м.");
    expect(mismatchText({ ...TURNED, rows: [], dist: undefined })).toBe("");
  });

  function turned(options = {}) {
    const wizard = { setOptions: vi.fn(async () => undefined), remove: vi.fn() };
    const props = { wizard: wizard as unknown as ObjectsWizard, file, options, un };
    return { wizard, tree: UnalignedChoice(props), markup: renderToStaticMarkup(createElement(UnalignedChoice, props)) };
  }

  it("shows the message, the rows, the distance and the three buttons, no tables, in the window of the step", () => {
    const { markup } = turned();
    const text = markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    expect(text).toContain("Центры блоков не совпали с сеткой");
    expect(text).toContain("расстояние 0.37 м");
    expect(text).toContain("Центры блоков в строках 1, 6, 10");
    expect(text).toContain("Наибольшее расхождение: 0,37 м");
    expect(markup).toContain('data-code="rotation_mismatch"');
    expect(markup).not.toContain('data-testid="unaligned-axes"');
    expect(markup).not.toContain('data-testid="unaligned-candidates"');
    for (const label of ["Задать сетку вручную", "Импортировать списком блоков", "Не импортировать этот файл"]) expect(text).toContain(label);
    expect(markup).not.toContain("confirm(");
  });

  it("«Задать сетку вручную» fills the form with the grid of the core (the turn included); the list and the way out work", () => {
    const { wizard, tree } = turned();
    find(tree, "unaligned-grid")!.props.onClick!();
    const [, patch] = wizard.setOptions.mock.calls[0] as unknown as [string, { blocks: { corner: string[]; rotation: string; div: string[] } }];
    expect(patch.blocks.corner).toEqual(["100", "200", "50"]);
    expect(patch.blocks.rotation).toBe("20");
    expect(patch.blocks.div).toEqual(["2", "2", "2"]);
    find(tree, "unaligned-list")!.props.onClick!();
    expect((wizard.setOptions.mock.calls[1] as unknown as [string, { blocks: { layout: string } }])[1].blocks.layout).toBe("list");
    find(tree, "unaligned-cancel")!.props.onClick!();
    expect(wizard.remove).toHaveBeenCalledWith("/tmp/Геомет.DAT");
  });
});
