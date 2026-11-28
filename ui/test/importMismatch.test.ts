/** Д-41 А2: a file that does not fit the section it was added to is not assigned a kind — the wizard shows the
 * choice «Импортировать как … / Сменить тип на … / Отменить» (the core builds it: `check` with `expected`). */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { startOpLog } from "../src/shell/opLog";

const post = vi.fn();
vi.mock("../src/shell/api", async (orig) => ({ ...(await orig<object>()), api: { post: (...a: unknown[]) => post(...a), get: vi.fn() } }));

import { MismatchNotice } from "../src/modules/importing/MismatchNotice";
import { expectedOf, filesOf, ready, Wizard, type CheckResult, type KindMismatch, type WizardMode } from "../src/modules/importing/wizard";

const mismatch: KindMismatch = {
  code: "kind_mismatch",
  message: "Раздел принимает «Устья скважин (Collar)», а файл похож на «Пробы (Assay)».",
  hint: "Выберите, как загрузить файл.",
  detected: "assay",
  expected: ["collar"],
  reason: "Файл распознан как «Пробы (Assay)»: «Номер скважины (HoleID)» ← «Hole-ID».",
  options: [
    { action: "import_as", kind: "collar", label: "Импортировать как «Устья скважин (Collar)»" },
    { action: "switch", kind: "assay", label: "Сменить тип на «Пробы (Assay)»" },
    { action: "cancel", kind: "", label: "Отменить" },
  ],
};

const mismatchOf = (detected: KindMismatch["detected"], expected: KindMismatch["expected"]): KindMismatch => ({
  ...mismatch,
  detected,
  expected,
  options: [...expected.map((k) => ({ action: "import_as" as const, kind: k, label: `Импортировать как ${k}` })), { action: "switch", kind: detected, label: `Сменить тип на ${detected}` }, mismatch.options[2]],
});

const result = (p: Partial<CheckResult>): CheckResult => ({
  rows: 9, columns: [], kind: null, mapping: {}, components: [], attributes: [], warnings: [], issues: [], preview: [], fields: [], ...p,
});

/** The core as the wizard sees it: inspect is trivial, check answers by the kind asked for. */
function core(detected: "assay" | "collar", expected: string[]) {
  post.mockImplementation(async (url: string, body: { kind: string | null; expected?: string[] }) => {
    if (url.endsWith("/inspect")) return { format: "csv", summary: "csv", info: {}, rows: [], numbers: [], warnings: [], size: 1 };
    const kind = body.kind ?? detected;
    if (!body.kind && body.expected && !body.expected.includes(kind)) return result({ mismatch: mismatchOf(kind as "assay", expected as "collar"[]) });
    return result({ kind: kind as "assay", mapping: { hole_id: "Hole-ID" } });
  });
}

const wizard = (mode: WizardMode = "holes") => new Wizard(startOpLog("Импорт скважин", "import"), mode);
const checks = () => post.mock.calls.filter(([url]) => String(url).endsWith("/check")).map(([, body]) => body);

beforeEach(() => {
  post.mockReset();
});

describe("the section a file is added to", () => {
  it("each step takes its own kinds; trenches take samples only", () => {
    expect(expectedOf("holes", "collar")).toEqual(["collar"]);
    expect(expectedOf("holes", "survey")).toEqual(["survey"]);
    expect(expectedOf("holes", "tables")).toEqual(["assay", "interval", "event"]);
    expect(expectedOf("trenches", "tables")).toEqual(["assay", "interval"]);
    expect(expectedOf("holes", "import")).toBeNull();
    expect(expectedOf("trenches", "collar")).toBeNull();
  });

  it("a file added to a section sends it as `expected`; a file opened with the window sends nothing", async () => {
    core("assay", ["collar"]);
    const w = wizard();
    await w.add(["/d/a.csv"], "collar");
    await w.add(["/d/b.csv"]);
    const [first, second] = checks();
    expect(first.expected).toEqual(["collar"]);
    expect("expected" in second).toBe(false);
  });
});

describe("a file that does not fit its section", () => {
  it("gets no kind, stays on the step it was added to and is not ready", async () => {
    core("assay", ["collar"]);
    const w = wizard();
    await w.add(["/d/ERR Core assay.csv"], "collar");
    const s = w.store.get();
    const f = s.files[0];
    expect(f.kind).toBeNull();
    expect(f.check?.mismatch?.detected).toBe("assay");
    expect(ready(f)).toBe(false);
    expect(filesOf(s, "collar")).toHaveLength(1); // shown on the step where the person is
    expect(filesOf(s, "tables")).toHaveLength(0); // not moved to the step of the kind found
    expect(s.files.some((x) => x.kind !== null)).toBe(false);
  });

  it("«Импортировать как»: the schema of the section is applied (kind given, no second guess)", async () => {
    core("assay", ["collar"]);
    const w = wizard();
    await w.add(["/d/a.csv"], "collar");
    const f = w.store.get().files[0];
    await w.resolveMismatch(f.id, mismatch.options[0]);
    const g = w.store.get().files[0];
    expect(g.kind).toBe("collar");
    expect(g.check?.mismatch).toBeUndefined();
    expect(checks().at(-1)).toMatchObject({ kind: "collar", mapping: null });
    expect(w.store.get().step).toBe("collar");
  });

  it("«Сменить тип»: the file takes the kind found and its step opens", async () => {
    core("assay", ["collar"]);
    const w = wizard();
    await w.add(["/d/a.csv"], "collar");
    await w.resolveMismatch(w.store.get().files[0].id, mismatch.options[1]);
    const s = w.store.get();
    expect(s.files[0].kind).toBe("assay");
    expect(s.step).toBe("tables");
    expect(filesOf(s, "tables")).toHaveLength(1);
  });

  it("«Отменить»: the file is dropped", async () => {
    core("assay", ["collar"]);
    const w = wizard();
    await w.add(["/d/a.csv"], "collar");
    await w.resolveMismatch(w.store.get().files[0].id, mismatch.options[2]);
    expect(w.store.get().files).toHaveLength(0);
  });

  it("a file that fits goes on without a question", async () => {
    core("collar", ["collar"]);
    const w = wizard();
    await w.add(["/d/c.csv"], "collar");
    expect(w.store.get().files[0]).toMatchObject({ kind: "collar" });
    expect(w.store.get().files[0].check?.mismatch).toBeUndefined();
  });

  it("in the mode of trenches the step of a collar does not exist: no «Сменить тип» to it", async () => {
    core("collar", ["assay", "interval"]);
    const w = wizard("trenches");
    w.go("tables");
    await w.add(["/d/c.csv"], "tables");
    const f = w.store.get().files[0];
    expect(f.check?.mismatch?.detected).toBe("collar");
    expect(w.mismatchOptions(f).map((o) => o.action)).toEqual(["import_as", "import_as", "cancel"]);
  });
});

describe("the plaque of the choice", () => {
  it("shows what the file looks like, why, and one button per way out (no browser dialogs)", async () => {
    core("assay", ["collar"]);
    const w = wizard();
    await w.add(["/d/a.csv"], "collar");
    const html = renderToStaticMarkup(createElement(MismatchNotice, { wizard: w, file: w.store.get().files[0] }));
    expect(html).toContain("Раздел принимает «Устья скважин (Collar)»");
    expect(html).toContain("Файл распознан как «Пробы (Assay)»");
    for (const label of ["Импортировать как collar", "Сменить тип на assay", "Отменить"]) expect(html).toContain(label);
    expect((html.match(/<button/g) ?? []).length).toBe(3);
  });

  it("renders nothing for a file without a mismatch", () => {
    const w = wizard();
    const file = { id: 1, path: "a", name: "a", options: { header_row: 1 }, inspect: null, check: result({ kind: "collar" }), kind: "collar" as const, mapping: {}, components: [], attributes: [], force: false, tableName: "a", busy: false, error: null };
    expect(renderToStaticMarkup(createElement(MismatchNotice, { wizard: w, file }))).toBe("");
  });
});
