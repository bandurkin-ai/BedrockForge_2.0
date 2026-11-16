import { useEffect, useRef, useState } from "react";
import { bridge } from "./bridge";
import { createApp } from "./shell/app";
import { AppContext } from "./shell/AppContext";
import { Shell } from "./shell/Shell";
import { fetchHealth } from "./shell/coreClient";
import { showMessage } from "./shell/dialogs";
import { useStore } from "./shell/useStore";

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
    const off = b?.onCoreState?.((st) => {
      app.store.set((s) => ({ core: st.status, coreVersion: st.info?.version ?? s.coreVersion, coreMessage: st.message ?? (st.status === "offline" ? s.coreMessage : null) }));
      if (st.status === "online" && st.message) void showMessage({ title: "Ядро", message: `${st.message}. Данные не потеряны; подробности — в журнале ядра.` });
    });
    return () => off?.();
  }, [app]);

  // ТЗ 7.0.2: versions of the interface and of the core must match.
  const coreVersion = useStore(app.store, (s) => s.coreVersion);
  useEffect(() => {
    const appVersion = bridge()?.appVersion();
    if (coreVersion && appVersion && coreVersion !== appVersion) {
      void showMessage({ title: "Разные версии", message: `Версия интерфейса ${appVersion}, ядра ${coreVersion}. Переустановите программу, чтобы версии совпали.` });
    }
  }, [coreVersion]);

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
