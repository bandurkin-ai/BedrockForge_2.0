/**
 * The host of the shared steps of the wizards (Э18, Э18б): «Модель и цель» (`StepTarget`) and «Пробы» (`StepSamples`) are the same in the
 * wizard «Оценка содержаний» and in the wizard «Моделирование» (Д-23: the same samples, domains and soft boundary), but each wizard has its
 * own form and its own store. A step reads its form, its issue and the defaults of the core from the host and writes through it; the
 * default host is the wizard of the estimation, the wizard of the simulation provides its own (`modules/simulation/host.ts`) with a
 * context. A form of a host is an `EstimationForm` (the simulation form extends it). No React components here.
 */
import { createContext, useContext } from "react";
import type { Store } from "../../shell/store";
import { loadBlockDetails } from "../blocks/state";
import { defaultName, type EstimationForm, type Issue } from "./form";
import { setForm, wizardStore, type DefaultsState } from "./wizardState";

/** What a shared step reads. */
export interface HostState {
  form: EstimationForm;
  issue: Issue | null;
  defaults: DefaultsState;
}

export interface FormHost {
  store: Store<HostState>;
  setForm(patch: Partial<EstimationForm>): void;
  /** the model chosen: the labels of its domain fields are in its details */
  chooseModel(id: number | null): void;
  /** the placeholder of the name of the row */
  defaultName(f: EstimationForm): string;
  /** the sub-blocks and the discretization of the block are asked (an estimation); a simulation works on the parents only */
  blockGrid: boolean;
}

/** The wizard of the estimation (the store of `wizardState.ts`; its `WizardState` has what `HostState` reads). */
export const estimationHost: FormHost = {
  store: wizardStore as unknown as Store<HostState>,
  setForm,
  chooseModel: (id) => {
    if (id !== null) void loadBlockDetails(id);
  },
  defaultName,
  blockGrid: true,
};

export const FormHostContext = createContext<FormHost>(estimationHost);
export const useHost = (): FormHost => useContext(FormHostContext);
