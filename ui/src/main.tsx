import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { bridge } from "./bridge";

const root = createRoot(document.getElementById("root")!);
const spike = bridge()?.spike() ?? new URLSearchParams(location.search).get("spike");

if (spike === "mesh") {
  // Spike Э0-б: three.js is a lazy chunk, never in the main bundle (ТЗ 17.4.8).
  void import("./spikes/mesh").then((m) => m.runMeshSpike(document.getElementById("root")!));
} else {
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
