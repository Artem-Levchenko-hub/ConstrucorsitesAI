import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { act } from "react";
import { createRoot } from "react-dom/client";
import ts from "typescript";
import { expect, it, vi } from "vitest";

it("refreshes the managed age marking from saved config on mount and return", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const source = readFileSync(resolve(process.cwd(), "../orchestrator/templates/max-miniapp-nextjs/src/components/OmniaCompliance.tsx"), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const readConfig = vi.fn().mockResolvedValue({ legal: { age_rating: "12+" } });
  const exports: { OmniaCompliance?: React.ComponentType } = {};
  new Function("exports", "require", compiled)(exports, (name: string) => {
    if (name === "react") return React;
    if (name === "react/jsx-runtime") return jsxRuntime;
    if (name.endsWith("integration-client")) return { getOmniaAppConfig: readConfig };
    if (name.endsWith("max-config")) return { omniaMaxConfig: { legal: { age_rating: "0+" } } };
    throw new Error(`Unexpected import ${name}`);
  });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    const Footer = exports.OmniaCompliance!;
    await act(async () => root.render(<Footer />));
    await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain("12+")); });
    readConfig.mockResolvedValue({ legal: { age_rating: "18+" } });
    await act(async () => window.dispatchEvent(new Event("focus")));
    await act(async () => { await vi.waitFor(() => expect(container.textContent).toContain("18+")); });
    readConfig.mockRejectedValue(new Error("offline"));
    await act(async () => window.dispatchEvent(new Event("focus")));
    expect(container.textContent).toContain("18+");
    expect(container.querySelectorAll("a")).toHaveLength(3);
    await act(async () => root.unmount());
    const calls = readConfig.mock.calls.length;
    window.dispatchEvent(new Event("focus"));
    expect(readConfig).toHaveBeenCalledTimes(calls);
  } finally { container.remove(); }
});
