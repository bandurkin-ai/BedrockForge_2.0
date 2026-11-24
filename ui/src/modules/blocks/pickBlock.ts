/**
 * «Выбранный блок» (ТЗ 8.7.6, ADR 0023 §11, decision 18): the block under a point is asked of the core
 * (`POST /viz/blocks/{id}/at`, float64, ≤ 100 ms), never read from the faces drawn — a face of a merged run or of
 * a LOD parent belongs to many blocks. The filter and the restrictor of the style of the model go with the question:
 * the answer says whether the block passes them (`passed: false` — a block that is there, hidden by the filter). Only
 * the newest question counts: an older answer does not overwrite it. A failure is a Russian text with its hint; a
 * missing value is `null`, never 0. No React, no three.js.
 */
import { errorText } from "../../shell/props";
import { viewStore } from "../scene/bind";
import { commonRequest, setPicked, styleIn, type PickedBlock } from "./display";
import { postAt, type AtBody, type Triple } from "./showApi";

let asked = 0;

/** The `at` body of the model by its style in the view: the point, the outward normal of the face, the filter, the restrictor. */
export function atBody(model: number, point: Triple, normal?: Triple, fields?: string[], keys: Record<string, unknown> = viewStore.get()): AtBody {
  return { point, ...(normal && { normal }), ...commonRequest(styleIn(keys, model)), ...(fields && { fields }) };
}

/**
 * Asks the core for the block at the point and keeps the answer in `pickedBlockStore`; resolves with that state (also
 * when it is not the newest — the caller of a command wants its own answer, the panel the newest).
 */
export async function lookupBlock(model: number, point: Triple, opts: { normal?: Triple; fields?: string[]; click?: Triple } = {}): Promise<PickedBlock> {
  const mine = ++asked;
  const shown = opts.click ?? point;
  const base = { model, point: shown };
  setPicked({ ...base, status: "loading", answer: null, error: null });
  let out: PickedBlock;
  try {
    const answer = await postAt(model, atBody(model, point, opts.normal, opts.fields));
    out = { ...base, status: answer.found ? "found" : "missing", answer, error: null };
  } catch (e) {
    out = { ...base, status: "error", answer: null, error: errorText(e) };
  }
  if (mine === asked) setPicked(out);
  return out;
}
