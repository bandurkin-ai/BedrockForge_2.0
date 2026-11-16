import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { bridge } from "./bridge";
import { Root } from "./Root";
import "./styles/tokens.css";

const host = document.getElementById("root")!;
const spike = bridge()?.spike() ?? new URLSearchParams(location.search).get("spike");

if (spike === "mesh") {
  // Spike Э0-б: three.js is a lazy chunk, never in the main bundle (ТЗ 17.4.8).
  void import("./spikes/mesh").then((m) => m.runMeshSpike(host));
} else {
  createRoot(host).render(
    <StrictMode>
      <Root />
    </StrictMode>,
  );
}
