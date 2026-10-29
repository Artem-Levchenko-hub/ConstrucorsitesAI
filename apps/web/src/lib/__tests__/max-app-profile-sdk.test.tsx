import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import ts from "typescript";
import { afterEach, expect, it, vi } from "vitest";

const readProfile = vi.fn();
const nodeRequire = createRequire(import.meta.url);
const source = readFileSync(resolve(process.cwd(),
  "../orchestrator/templates/max-miniapp-nextjs/src/lib/omnia/app-profile-client.ts"), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const loaded = { exports: {} as Record<string, unknown> };
new Function("require", "module", "exports", compiled)(
  (id: string) => id === "@/lib/omnia/app-profile-actions"
    ? { getYleumAppProfile: readProfile } : nodeRequire(id), loaded, loaded.exports,
);
const useProfile = loaded.exports.useYleumAppProfile as () => {
  profile: { app_name: string; summary: string; support: { email: string | null } } | null;
  configVersion: number | null; status: string; refresh: () => Promise<void>;
};
const roots: Array<{ root: Root; node: HTMLDivElement }> = [];
async function render() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  let current!: ReturnType<typeof useProfile>;
  function Harness() { current = useProfile(); return null; }
  const node = document.createElement("div"); document.body.append(node);
  const root = createRoot(node); roots.push({ root, node });
  await act(async () => root.render(<Harness />));
  return { get current() { return current; } };
}
const response = (version: number) => ({ ok: true, configVersion: version,
  profile: { app_name: `Мастерская ${version}`, summary: "Запись к мастеру", support: { email: "help@example.ru", response_time: "Днём" } },
});
afterEach(async () => {
  for (const { root, node } of roots.splice(0)) { await act(async () => root.unmount()); node.remove(); }
  vi.restoreAllMocks(); readProfile.mockReset();
});

it("reads changed public name and contacts on focus without a release", async () => {
  readProfile.mockResolvedValueOnce(response(1)).mockResolvedValueOnce(response(2));
  const state = await render();
  expect(state.current.profile?.app_name).toBe("Мастерская 1");
  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(state.current.profile?.app_name).toBe("Мастерская 2");
  expect(state.current.profile?.support.email).toBe("help@example.ru");
  expect(state.current.configVersion).toBe(2);
});

it("does not show a bundled profile when the public source is unavailable", async () => {
  readProfile.mockResolvedValueOnce(response(1)).mockResolvedValueOnce({ ok: false });
  const state = await render();
  await act(async () => state.current.refresh());
  expect(state.current.status).toBe("error");
  expect(state.current.profile).toBeNull();
});

it("does not request offline and recovers on reconnect", async () => {
  const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
  readProfile.mockResolvedValue(response(3));
  const state = await render();
  expect(readProfile).not.toHaveBeenCalled();
  expect(state.current.status).toBe("error");
  online.mockReturnValue(true);
  await act(async () => window.dispatchEvent(new Event("online")));
  expect(state.current.configVersion).toBe(3);
});
