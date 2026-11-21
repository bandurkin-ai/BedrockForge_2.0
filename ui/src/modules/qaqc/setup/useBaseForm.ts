/**
 * The common life of a form of «Настройка QAQC» over the stored values (ADR 0020 §3): it starts from the stored
 * version and follows newer versions while it has no edits; with edits it keeps them and says the setup was
 * changed elsewhere; a 409 resets it to the stored values; its «Сохранить» runs the command with the version the
 * form was started from (`expected_version`).
 */
import { useEffect, useRef, useState } from "react";
import { useApp } from "../../../shell/AppContext";
import { configStore, ensureLoaded, setBase, type ConfigState, type Part } from "./configStore";
import { useSnap } from "./useSnap";

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export function useBaseForm<T>(part: Part, make: (s: ConfigState) => T | null, onDirty: (on: boolean) => void) {
  const app = useApp();
  const st = useSnap(configStore, (s) => s);
  const stored = part === "config" ? st.config : st.crm;
  const [form, setForm] = useState<T | null>(() => make(st));
  const [base, setBaseForm] = useState<T | null>(form);
  const [version, setVersion] = useState<number | null>(stored?.version ?? null);
  const [outside, setOutside] = useState(false);
  const conflictSeq = st.conflict?.part === part ? st.conflict.seq : 0;
  const lastSeq = useRef(conflictSeq);
  const dirty = form !== null && base !== null && !same(form, base);

  useEffect(() => ensureLoaded(), []);
  useEffect(() => {
    if (!stored) return;
    const now = make(configStore.get());
    // a 409, no edits, or the form is what was stored (its own save): the form follows the stored values
    if (conflictSeq !== lastSeq.current || !dirty || same(now, form)) {
      setForm(now);
      setBaseForm(now);
      setVersion(stored.version);
      setOutside(false);
      lastSeq.current = conflictSeq;
    } else if (stored.version !== version) setOutside(true);
  }, [stored?.version, conflictSeq]);
  useEffect(() => onDirty(dirty), [dirty]);

  const save = (command: string, args: Record<string, unknown>) => {
    setBase(part, version);
    void app.run(command, args);
  };
  /** «Отменить правки»: the stored values as they are now. */
  const undo = () => {
    const now = make(configStore.get());
    setForm(now);
    setBaseForm(now);
    setVersion(stored?.version ?? null);
    setOutside(false);
  };
  const conflict = st.conflict?.part === part ? st.conflict.message : null;
  return { app, st, form, setForm, base, dirty, outside, conflict, save, undo };
}
