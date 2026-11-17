import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Root } from "./Root";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/montserrat/500.css";
import "@fontsource/montserrat/600.css";
import "@fontsource/montserrat/700.css";
import "./styles/tokens.css";

const host = document.getElementById("root")!;

// The 3D window is a lazy chunk (modules/scene/SceneView → viz/Scene): three.js is never in the main bundle (ТЗ 17.4.8).
createRoot(host).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
