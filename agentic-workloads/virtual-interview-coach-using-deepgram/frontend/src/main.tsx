// React entry point (T027) — mounts the single G1 screen.

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import ErrorBoundary from "./components/ErrorBoundary";
import Session from "./pages/Session";
import "./styles/theme.css";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("root element missing");

createRoot(rootEl).render(
  <StrictMode>
    <ErrorBoundary>
      <Session />
    </ErrorBoundary>
  </StrictMode>
);
