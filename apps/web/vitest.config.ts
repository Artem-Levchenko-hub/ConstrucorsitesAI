import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Headless behavioural harness for the streaming-preview layer (V3.0b). The
// bootstrap script (streaming-preview-bootstrap.ts) ships pure runtime DOM
// behaviour — morphdom patching, image-preservation, brief transport — that
// `tsc`/`next build` only type-check, never exercise. jsdom gives us a real
// DOM + DOMParser + MessageEvent so each generator-wide live-render invariant
// becomes a permanent falsifiable assert (ratchet), money-free, 0 LLM.
export default defineConfig({
  oxc: {
    // Next keeps JSX intact for its own compiler. Vitest needs the automatic
    // runtime so component-level interaction tests can import client TSX.
    jsx: {
      importSource: "react",
      runtime: "automatic",
    },
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
