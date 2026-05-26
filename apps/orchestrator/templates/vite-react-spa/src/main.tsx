import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import "./index.css";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("root element missing — check index.html");

createRoot(rootEl).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
