"use client";

import { lazy, Suspense, useEffect, useState } from "react";

// Keep generated code out of SSR without relying on next/dynamic's client-only
// boundary. In dev/Turbopack that boundary can load the ProductApp chunk yet
// leave its host empty after hydration. React.lazy is activated only after the
// first browser commit, so SSR and the first hydration frame stay identical.
const ProductApp = lazy(() => import("@/components/product/ProductApp"));

export function OmniaProductRuntime() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  return (
    <div data-omnia-product-runtime="true" style={{ display: "contents" }}>
      {mounted ? (
        <Suspense fallback={null}>
          <ProductApp />
        </Suspense>
      ) : null}
    </div>
  );
}
