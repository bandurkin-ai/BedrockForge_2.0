import { useEffect, useRef, useState } from "react";
import { bridge } from "./bridge";
import { fetchHealth, type Health } from "./shell/coreClient";

const BRAND = "BedrockForge by Mikhail Bandurkin";

/** Э0 placeholder window: brand and core status. The real shell arrives on Э1. */
export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const readySent = useRef(false);

  useEffect(() => {
    const core = bridge()?.core();
    if (!core) {
      setError("Окно открыто без оболочки BedrockForge: ядро недоступно.");
      return;
    }
    fetchHealth(core)
      .then(setHealth)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (readySent.current) return; // StrictMode runs effects twice
    readySent.current = true;
    requestAnimationFrame(() => bridge()?.uiReady());
  }, []);

  return (
    <main style={{ fontFamily: "Segoe UI, sans-serif", color: "#e6e8eb", background: "#15181d", height: "100vh", margin: 0, padding: 24, boxSizing: "border-box" }}>
      <h1 style={{ fontWeight: 600, fontSize: 22 }}>{BRAND}</h1>
      <p data-testid="core-status">
        {health ? `Ядро ${health.version} работает (${health.mode})` : error ?? "Подключение к ядру…"}
      </p>
    </main>
  );
}
