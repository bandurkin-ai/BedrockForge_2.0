/**
 * The search ellipsoid, the block and the probes of the preview in the main 3D (ТЗ 8.8.4: «в 3D у выбранного блока — эллипсоид, отобранные
 * пробы и их веса»; docs/progress/e18-api.md §7, §12 `estimate.ellipsoid`) — what is drawn, as numbers, and what the command wants shown.
 * Two sources: the wizard (the ellipsoid of the pass chosen from the form of step 4 — the radii and the angles typed, «по модели» —
 * from `defaults.search`, the radii × the factor of the pass — at the centre of the block of the last preview, which also gives the
 * probes) and a stored row asked for by the command (`estimate.ellipsoid {id}`: the preview of the row at a block). The form is
 * followed live: an edit of step 4 changes only the pose (`scene.ts`), the probes stay those of the last «Показать» (the wizard marks
 * them «устарел»). A form that cannot be drawn now (a half-typed number) is `pose: null` — the layer keeps the pose it has. No three.js, no React.
 */
import { Store } from "../../shell/store";
import type { Vec3 } from "../../viz/core/vec3";
import { buildSegments, type SegmentsBuild } from "./inspect/build";
import type { Candidate, InspectResult, Segment } from "./inspect/api";
import { SELECTED } from "./inspect/view";
import { effectiveFrom, hasVariogram, passesOf } from "./formSearch";
import { REASON_LABELS, type PreviewAnswer, type PreviewEllipsoid } from "./previewApi";
import type { DefaultsAnswer } from "./api";
import type { EstimationForm } from "./form";
import type { WizardState } from "./wizardState";

/** The angles (°) and the radii (m) of an ellipsoid as `viz/ellipsoid` takes them. */
export interface Pose {
  ang1: number;
  ang2: number;
  ang3: number;
  r1: number;
  r2: number;
  r3: number;
}

export interface PreviewSceneState {
  /** the layer of the wizard is wanted (`estimate.ellipsoid {show}`): drawn while a window of the form is open and there is a block shown */
  wizard: boolean;
  /** the pass of the ellipsoid, from 1; null — the one that gave the estimate, else the first */
  pass: number | null;
  /** a stored row asked for by the command: its own answer of the core (the layer stays until it is taken away) */
  row: { id: number; name: string; answer: PreviewAnswer } | null;
  /** the command asked to bring the view to the layers (it counts up; the scene fits the camera once per number) */
  reveal: number;
}

export const EMPTY_SCENE: PreviewSceneState = { wizard: true, pass: null, row: null, reveal: 0 };

export const previewSceneStore = new Store<PreviewSceneState>(EMPTY_SCENE);

/** The choice of the select of step 4: "auto" — the pass that gave the estimate, a number — that pass, "off" — not in 3D. */
export function chooseView(value: string): void {
  if (value === "off") previewSceneStore.set({ wizard: false });
  else previewSceneStore.set({ wizard: true, pass: value === "auto" ? null : Number(value) });
}

export const resetPreviewScene = (): void => previewSceneStore.set((s) => ({ ...EMPTY_SCENE, reveal: s.reveal }));

export interface PreviewView {
  source: "wizard" | "row";
  /** the answer the block and the probes come from: another one — they are built again */
  answer: PreviewAnswer;
  centre: Vec3;
  size: Vec3 | null;
  /** the pose of the ellipsoid; null — nothing can be drawn from the form now (the layer keeps what it has) */
  pose: Pose | null;
  /** the pass of the pose; null — unknown (a row without an ellipsoid) */
  pass: number | null;
}

/** «0,5» → 0.5; empty or not a number — null. */
export function numberOf(text: string): number | null {
  const t = text.trim().replace(",", ".");
  if (!t) return null;
  const v = Number(t);
  return Number.isFinite(v) ? v : null;
}

const posed = (e: Pick<PreviewEllipsoid, "ang1" | "ang2" | "ang3" | "r1" | "r2" | "r3">): Pose => ({ ang1: e.ang1, ang2: e.ang2, ang3: e.ang3, r1: e.r1, r2: e.r2, r3: e.r3 });

/** The pose can be drawn: the radii are positive numbers. */
export const drawablePose = (p: Pose | null): p is Pose => p !== null && [p.r1, p.r2, p.r3].every((x) => Number.isFinite(x) && x > 0) && [p.ang1, p.ang2, p.ang3].every(Number.isFinite);

/** The pass drawn: the one asked, else the one that gave the estimate, else the first; kept within the passes there are. */
export function chosenPass(asked: number | null, answer: Pick<PreviewAnswer, "pass" | "ellipsoid"> | null, count: number): number {
  const want = asked ?? answer?.pass ?? answer?.ellipsoid?.pass ?? 1;
  return Math.min(Math.max(1, Math.floor(want)), Math.max(1, count));
}

/**
 * The ellipsoid of the search from the form of step 4 (before the factor of the pass): by hand — the numbers typed (r1 is needed, r2
 * and r3 without a value are r1, an empty angle is 0), «по модели» — what the core took (`defaults.search`). null — it cannot be told
 * from the form now; `"answer"` — the form does not say (a trend, a model with no defaults): the ellipsoid of the last preview is drawn.
 */
export function formPose(f: Pick<EstimationForm, "search" | "method" | "ikModels" | "variogramIds" | "variogramId">, defaults: Pick<DefaultsAnswer, "search"> | null): Pose | null | "answer" {
  const from = effectiveFrom(f.search, hasVariogram(f));
  if (from === "trend") return "answer";
  if (from === "model") {
    const s = defaults?.search;
    if (!s) return "answer";
    return [s.ang1, s.ang2, s.ang3, s.r1, s.r2, s.r3].every((x) => x !== null) ? { ang1: s.ang1!, ang2: s.ang2!, ang3: s.ang3!, r1: s.r1!, r2: s.r2!, r3: s.r3! } : "answer";
  }
  const r = f.search.r.map(numberOf);
  if (r[0] === null) return null;
  const ang = f.search.ang.map((t) => (t.trim() ? numberOf(t) : 0));
  if (ang.some((x) => x === null) || r.slice(1).some((x, i) => x === null && f.search.r[i + 1].trim())) return null;
  const pose: Pose = { ang1: ang[0]!, ang2: ang[1]!, ang3: ang[2]!, r1: r[0], r2: r[1] ?? r[0], r3: r[2] ?? r[0] };
  return drawablePose(pose) ? pose : null;
}

/** The pose of the pass: the ellipsoid of the form × the factor of the pass (the passes typed, else those of the core); null — not now. */
export function wizardPose(w: Pick<WizardState, "form" | "defaults">, answer: PreviewAnswer, asked: number | null): { pose: Pose | null; pass: number | null } {
  const passes = passesOf(w.form.search, w.defaults.data?.params);
  const pass = chosenPass(asked, answer, passes.length);
  const base = formPose(w.form, w.defaults.data);
  if (base === "answer") {
    const e = answer.ellipsoid;
    return { pose: e && drawablePose(posed(e)) ? posed(e) : null, pass: e?.pass ?? null };
  }
  const factor = numberOf(passes[pass - 1]?.factor ?? "");
  if (!base || factor === null || !(factor > 0)) return { pose: null, pass };
  return { pose: { ...base, r1: base.r1 * factor, r2: base.r2 * factor, r3: base.r3 * factor }, pass };
}

/** The centre of the block of an answer. */
const centreOf = (a: PreviewAnswer): Vec3 | null => a.centre ?? a.ellipsoid?.centre ?? null;

/** What is drawn now, or null: a row asked for by the command first, else the wizard (a window of the form is open, a block was shown, the layer is wanted). */
export function viewOf(s: PreviewSceneState, w: Pick<WizardState, "form" | "defaults" | "preview">, formOpen: boolean): PreviewView | null {
  if (s.row) {
    const a = s.row.answer;
    const centre = centreOf(a);
    const e = a.ellipsoid;
    if (!centre) return null;
    return { source: "row", answer: a, centre, size: a.size, pose: e && drawablePose(posed(e)) ? posed(e) : null, pass: e?.pass ?? null };
  }
  const a = w.preview.data;
  if (!s.wizard || !formOpen || !a) return null;
  const centre = centreOf(a);
  if (!centre) return null;
  const { pose, pass } = wizardPose(w, a, s.pass);
  return { source: "wizard", answer: a, centre, size: a.size, pose, pass };
}

// ---------------------------------------------------------------------------------------------------- the probes

/**
 * The probes of the preview in the shape the segments of the inspector are built from (`inspect/build.ts`, one look for both): a probe
 * with a weight is taken (a segment from the centre of the block, the colour — λ: Cobalt for a positive one, Copper Orange for a
 * negative one), the others are points in Slate (a line to those cut off by a limit, none to those outside the ellipsoid).
 */
export function probesOf(a: PreviewAnswer): Pick<InspectResult, "candidates" | "segments" | "ellipsoid"> {
  const centre = centreOf(a) ?? [0, 0, 0];
  const candidates: Candidate[] = [];
  const segments: Segment[] = [];
  for (const s of a.samples) {
    if (!s.xyz) continue;
    const taken = s.lambda !== null;
    // a probe cut to t («урезана до t») has a weight too: it is a segment, not a second point
    const reason = taken ? SELECTED : s.reason;
    candidates.push({ i: s.i, row_id: s.row_id, hole: s.hole, from: s.from, to: s.to, xyz: s.xyz, h: s.h, dist: s.dist, octant: s.octant, z_raw: s.z_raw, z: s.z, lambda: s.lambda, reason, reason_label: REASON_LABELS[s.reason] ?? s.reason, merged: 1, domain: null });
    if (taken) segments.push({ i: s.i, from: [centre[0], centre[1], centre[2]], to: s.xyz, lambda: s.lambda, z: s.z });
  }
  return { candidates, segments, ellipsoid: null };
}

export const buildProbes = (a: PreviewAnswer): SegmentsBuild => buildSegments(probesOf(a), centreOf(a) ?? [0, 0, 0]);

/** The edges of the block, two points each: the 12 edges of the box of the size `size` about the centre, from the integer origin of the centre. */
export interface BoxBuild {
  origin: { x: number; y: number; z: number };
  positions: Float32Array;
  /** absolute [xmin, ymin, zmin, xmax, ymax, zmax] */
  reach: [number, number, number, number, number, number];
}

const CORNERS: [number, number, number][] = [
  [-1, -1, -1],
  [1, -1, -1],
  [1, 1, -1],
  [-1, 1, -1],
  [-1, -1, 1],
  [1, -1, 1],
  [1, 1, 1],
  [-1, 1, 1],
];
const EDGES: [number, number][] = [
  [0, 1],
  [1, 2],
  [2, 3],
  [3, 0],
  [4, 5],
  [5, 6],
  [6, 7],
  [7, 4],
  [0, 4],
  [1, 5],
  [2, 6],
  [3, 7],
];

export function buildBox(centre: Vec3, size: Vec3): BoxBuild {
  const origin = { x: Math.round(centre[0]), y: Math.round(centre[1]), z: Math.round(centre[2]) };
  const rel = [centre[0] - origin.x, centre[1] - origin.y, centre[2] - origin.z];
  const corner = (c: [number, number, number]): number[] => c.map((s, k) => rel[k] + (s * size[k]) / 2);
  const positions = new Float32Array(EDGES.length * 6);
  EDGES.forEach(([a, b], e) => positions.set([...corner(CORNERS[a]), ...corner(CORNERS[b])], e * 6));
  return { origin, positions, reach: [centre[0] - size[0] / 2, centre[1] - size[1] / 2, centre[2] - size[2] / 2, centre[0] + size[0] / 2, centre[1] + size[1] / 2, centre[2] + size[2] / 2] };
}

// ---------------------------------------------------------------------------------------------------- the words

const metres = (p: Pose): string => [p.r1, p.r2, p.r3].map((x) => x.toLocaleString("ru-RU", { maximumSignificantDigits: 5 })).join(" × ");

/** The answer of `estimate.ellipsoid`: what is shown where. */
export function shownText(v: PreviewView): string {
  const at = v.answer.at.ijk ? v.answer.at.ijk.join("; ") : `№${v.answer.at.row}`;
  const ell = v.pose ? `Эллипсоид поиска${v.pass !== null ? ` прохода ${v.pass}` : ""}: радиусы ${metres(v.pose)} м` : "Эллипсоид не показан: форма не позволяет его построить";
  return `${ell}; блок ${at}${v.answer.label ? `, домен «${v.answer.label}»` : ""}; проб в оценке ${v.answer.counts.selected} из ${v.answer.counts.candidates}`;
}
