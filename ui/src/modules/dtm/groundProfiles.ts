/**
 * The profiles of «Фильтр земли» as the core has them (`GET /lines/ground/profiles`: «Карьер», «Равнина», their
 * numbers and hints, the ranges of the parameters — `geo/ground.py`, `lines/ground_job.py`). The interface keeps
 * no table of its own: `groundProfiles` holds the spare values (`ground.ts` `SPARE`, `loaded: false`) until the
 * core answers, then the answer. The answer is asked once; a failure leaves the spare values and the next
 * call asks again.
 */
import { useEffect } from "react";
import { api } from "../../shell/api";
import { Store } from "../../shell/store";
import { useStore } from "../../shell/useStore";
import { parseProfiles, SPARE, type GroundProfiles } from "./ground";

export const groundProfiles = new Store<GroundProfiles>(SPARE);

let asked: Promise<GroundProfiles> | null = null;

/** The profiles of the core, asked once. `get` — the request (tests put their own). */
export function loadGroundProfiles(get: (path: string) => Promise<unknown> = api.get): Promise<GroundProfiles> {
  if (groundProfiles.get().loaded) return Promise.resolve(groundProfiles.get());
  asked ??= get("/lines/ground/profiles")
    .then((raw) => {
      const parsed = parseProfiles(raw);
      if (parsed) groundProfiles.set(parsed);
      return groundProfiles.get();
    })
    .catch(() => groundProfiles.get()) // the core is away: the spare values stay, the window works
    .finally(() => {
      if (!groundProfiles.get().loaded) asked = null;
    });
  return asked;
}

/** Tests: back to the spare values. */
export function resetGroundProfiles(): void {
  asked = null;
  groundProfiles.set(SPARE);
}

/** The profiles for a component: the spare values at once, the answer of the core when it comes. */
export function useGroundProfiles(): GroundProfiles {
  const table = useStore(groundProfiles, (s) => s);
  useEffect(() => {
    void loadGroundProfiles();
  }, []);
  return table;
}
