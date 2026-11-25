/**
 * The host of the wizard «Моделирование» for the steps it shares with the estimation (`StepTarget`, `StepSamples`; `estimation/host.ts`):
 * the form, the issue and the defaults of the core are those of the store of the simulation, the sub-blocks and the discretization
 * are not asked (the nodes of a simulation are the parents), the default name is «‹поле› · ‹ВИД› · ‹домен›». No React components here.
 */
import type { Store } from "../../shell/store";
import type { FormHost, HostState } from "../estimation/host";
import { simDefaultName } from "./form";
import { chooseModel, setForm, simWizardStore } from "./wizardState";

export const simulationHost: FormHost = {
  store: simWizardStore as unknown as Store<HostState>,
  setForm: (patch) => setForm(patch),
  chooseModel,
  defaultName: simDefaultName,
  blockGrid: false,
};
