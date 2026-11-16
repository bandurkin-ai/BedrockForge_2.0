import { useEffect, useRef, useState } from "react";
import { bridge } from "./bridge";
import { createApp } from "./shell/app";
import { AppContext } from "./shell/AppContext";
import { Shell } from "./shell/Shell";
import { fetchHealth } from "./shell/coreClient";

/** Creates the app once, tracks the core and tells the shell window it has drawn (one splash). */
export function Root() {
  const [app] = useState(() => createApp());
  const readySent = useRef(false);

  useEffect(() => {
    const b = bridge();
    const core = b?.core();
    if (!core) {
      app.store.set({ core: "offline" });
    } else {
      fetchHealth(core)
        .then((h) => app.store.set({ core: "online", coreVersion: h.version }))
        .catch(() => app.store.set({ core: "offline" }));
    }
    b?.onCoreState?.((st) => app.store.set({ core: st.status, coreVersion: st.info?.version ?? null }));
  }, [app]);

  useEffect(() => {
    if (readySent.current) return; // StrictMode runs effects twice (ТЗ 17.4.9)
    readySent.current = true;
    requestAnimationFrame(() => bridge()?.uiReady());
  }, []);

  return (
    <AppContext.Provider value={app}>
      <Shell />
    </AppContext.Provider>
  );
}
