import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { ErrorBoundary } from "./shell/ErrorBoundary";
import "./styles.css";
import "./shell/shell.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary label="BedrockForge"><App /></ErrorBoundary>
  </StrictMode>,
);
