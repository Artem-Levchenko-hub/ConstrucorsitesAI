import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import ts from "typescript";

const template = resolve(process.cwd(), "../orchestrator/templates/max-miniapp-nextjs/src");
const nodeRequire = createRequire(import.meta.url);

/** One real template dependency graph per harness; only explicit external seams
 * are replaced. In particular identity and auth recovery keep shared state. */
export function createMaxTemplateLoader(imports: Record<string, unknown> = {}, boundary: Window = window) {
  const cache = new Map<string, { exports: Record<string, unknown> }>();
  function evaluate(source: string, filename: string) {
    const loaded = { exports: {} as Record<string, unknown> };
    cache.set(filename, loaded);
    const output = ts.transpileModule(source, { fileName: filename, compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText;
    new Function("require", "module", "exports", "window", "fetch", output)((id: string) => {
      if (Object.hasOwn(imports, id)) return imports[id];
      if (id.startsWith("@/")) return load(resolve(template, id.slice(2)));
      if (id.startsWith(".")) return load(resolve(dirname(filename), id));
      return nodeRequire(id);
    }, loaded, loaded.exports, boundary, (...args: Parameters<typeof window.fetch>) => boundary.fetch(...args));
    return loaded.exports;
  }
  function load(path: string): Record<string, unknown> {
    const filename = [path, `${path}.ts`, `${path}.tsx`].find(candidate => existsSync(candidate));
    if (!filename) throw new Error(`Missing template test dependency: ${path}`);
    return cache.get(filename)?.exports ?? evaluate(readFileSync(filename, "utf8"), filename);
  }
  return { evaluate, load: (path: string) => load(resolve(template, path)) };
}
