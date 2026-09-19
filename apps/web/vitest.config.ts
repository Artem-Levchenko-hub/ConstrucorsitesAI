import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Headless behavioural harness: jsdom gives components and hooks a real DOM,
// DOMParser and MessageEvent, so UI invariants become permanent asserts.
export default defineConfig({
  oxc: {
    jsx: {
      runtime: "automatic",
      importSource: "react",
    },
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      // `server-only` resolves only inside a Next build.
      "server-only": fileURLToPath(new URL("./src/test/server-only.ts", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
