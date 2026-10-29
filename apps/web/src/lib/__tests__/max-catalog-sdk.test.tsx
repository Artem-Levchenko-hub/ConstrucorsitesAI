import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import ts from "typescript";
import { afterEach, expect, it, vi } from "vitest";

const readCatalog = vi.fn();
const nodeRequire = createRequire(import.meta.url);
// Execute the real shipped hook with the same React instance as the renderer;
// replace only its network boundary, whose server/file contract has Node tests.
const source = readFileSync(resolve(process.cwd(),
  "../orchestrator/templates/max-miniapp-nextjs/src/lib/omnia/catalog-client.ts",
), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const compiledModule = { exports: {} as Record<string, unknown> };
new Function("require", "module", "exports", compiled)(
  (id: string) => id === "@/lib/omnia/catalog-actions"
    ? { getYleumCatalog: readCatalog } : nodeRequire(id),
  compiledModule, compiledModule.exports,
);
const useCatalog = compiledModule.exports.useYleumCatalog as () => {
  configVersion: number | null; status: string; error: string | null;
  items: { id: string }[]; refresh: () => Promise<void>;
};
const response = (version: number) => ({
  ok: true, configVersion: version, items: [{ id: `item-${version}` }],
});
function deferred() {
  let resolve!: (value: ReturnType<typeof response>) => void;
  const promise = new Promise<ReturnType<typeof response>>(done => { resolve = done; });
  return { promise, resolve };
}
const roots: Array<{ root: Root; node: HTMLDivElement }> = [];
async function renderCatalog() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  let current!: ReturnType<typeof useCatalog>;
  function Harness() { current = useCatalog(); return null; }
  const node = document.createElement("div"); document.body.append(node);
  const root = createRoot(node); roots.push({ root, node });
  await act(async () => root.render(<Harness />));
  return { result: { get current() { return current; } } };
}
afterEach(async () => {
  for (const { root, node } of roots.splice(0)) {
    await act(async () => root.unmount()); node.remove();
  }
  vi.restoreAllMocks(); readCatalog.mockReset();
});

it("focus rereads the catalog and a late older response cannot overwrite it", async () => {
  const first = deferred(); const focused = deferred();
  readCatalog.mockReturnValueOnce(first.promise).mockReturnValueOnce(focused.promise);
  const { result } = await renderCatalog();
  act(() => window.dispatchEvent(new Event("focus")));
  expect(readCatalog).toHaveBeenCalledTimes(2);
  await act(async () => focused.resolve(response(8)));
  expect(result.current.configVersion).toBe(8);
  await act(async () => first.resolve(response(7)));
  expect(result.current.configVersion).toBe(8);
  expect(result.current.items).toEqual([{ id: "item-8" }]);
});

it("starts honestly offline and reloads after reconnect", async () => {
  const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
  readCatalog.mockResolvedValue(response(3));
  const { result } = await renderCatalog();
  expect(readCatalog).not.toHaveBeenCalled();
  expect(result.current.status).toBe("error");
  expect(result.current.items).toEqual([]);
  online.mockReturnValue(true);
  act(() => window.dispatchEvent(new Event("online")));
  await act(async () => {});
  expect(result.current.configVersion).toBe(3);
});

it("clears stale positions on a failed read and recovers on refresh", async () => {
  readCatalog.mockResolvedValueOnce(response(1)).mockResolvedValueOnce({ ok: false })
    .mockResolvedValueOnce(response(2));
  const { result } = await renderCatalog();
  expect(result.current.status).toBe("ready");
  await act(async () => result.current.refresh());
  expect(result.current.status).toBe("error");
  expect(result.current.items).toEqual([]);
  expect(result.current.configVersion).toBeNull();
  await act(async () => result.current.refresh());
  expect(result.current.configVersion).toBe(2);
});
