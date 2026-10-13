import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import type { Config } from "./auth";
import "./styles.css";

fetch("/config.json")
  .then((r) => r.json() as Promise<Config>)
  .then((cfg) =>
    createRoot(document.getElementById("root")!).render(
      <StrictMode>
        <App cfg={cfg} />
      </StrictMode>,
    ),
  );
