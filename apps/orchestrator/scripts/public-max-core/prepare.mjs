// Build-time overlay of the trusted kit only. Never run against generated apps.
import { readFileSync, writeFileSync } from "node:fs";

function edit(path, transform) {
  const original = readFileSync(path, "utf8");
  const changed = transform(original);
  if (changed === original) throw new Error(`Missing expected trusted source: ${path}`);
  writeFileSync(path, changed);
}

const configImport = 'import { omniaMaxConfig as app } from "@/lib/omnia/max-config";';
const runtimeImport = 'import { getMaxConfig } from "@/lib/omnia/runtime-config";';
for (const route of ["support", "legal/privacy", "legal/terms"]) {
  edit(`src/app/${route}/page.tsx`, (source) => {
    if (!source.includes(configImport)
        || !source.includes("export const metadata = ")
        || !source.includes("export default function ")) throw new Error("Unexpected legal page");
    return source.replace(configImport, runtimeImport)
      .replace(/export const metadata = (\{[^\n]+\});/,
        "export function generateMetadata() { const app = getMaxConfig(); return $1; }")
      .replace(/(export default function \w+\(\) \{)/, "$1\n  const app = getMaxConfig();");
  });
}
edit("src/lib/max/bot-api.ts", (source) => {
  const handlers = /(export async function sendMax(?:Welcome|Help)\([\s\S]*?\): Promise<void> \{)/g;
  if (!source.includes(configImport)
      || [...source.matchAll(handlers)].length !== 2) throw new Error("Unexpected bot API");
  return source.replace(configImport, runtimeImport)
    .replace(handlers, "$1\n  const app = getMaxConfig();");
});
// Compile once; owner preview remains an explicit controller-only runtime mode.
// NODE_ENV is folded to production by Next, so it cannot authorize this route.
edit("src/app/api/omnia/preview-session/route.ts", (source) => source.replace(
  'process.env.NODE_ENV !== "development" || !projectId',
  'process.env.OMNIA_OWNER_PREVIEW !== "1" || process.env.OMNIA_PUBLIC_APP_ORIGIN || !projectId',
));
writeFileSync("src/app/layout.tsx", `
export const dynamic = "force-dynamic";
export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="ru"><body style={{margin:0,fontFamily:"system-ui",color:"#17202a",background:"#fff"}}>{children}</body></html>;
}
`);
writeFileSync("src/lib/omnia/runtime-config.ts", `
import { existsSync, readFileSync } from "node:fs";
import type { OmniaMaxConfig } from "./max-config";
import { omniaMaxConfig as initialPreviewConfig } from "./max-config";
export function getMaxConfig(): OmniaMaxConfig {
  // A first owner preview may not have saved business metadata yet. Preserve
  // the trusted kit's initial values; published apps still require saved data.
  if (process.env.OMNIA_OWNER_PREVIEW === "1" && !process.env.OMNIA_PUBLIC_APP_ORIGIN
      && !existsSync("/app/omnia-business-config.json")) return initialPreviewConfig;
  // Missing public/invalid config fails closed; the controller must finish readback
  // before publishing ingress. No build-time placeholders are served.
  return JSON.parse(readFileSync("/app/omnia-business-config.json", "utf8"));
}
`);
writeFileSync("src/app/api/omnia/config/route.ts", `
import { NextResponse } from "next/server";
import { getMaxConfig } from "@/lib/omnia/runtime-config";
export const dynamic = "force-dynamic";
export function GET() {
  return NextResponse.json(getMaxConfig(), { headers: { "Cache-Control": "no-store" } });
}
`);

// Bound build parallelism without changing any template used by the agent.
edit("next.config.ts", (source) => source.replace(
  'output: "standalone",', 'output: "standalone",\n  experimental: { cpus: 1 },',
));
