"use client";

import dynamic from "next/dynamic";

// Product code is intentionally loaded only in the browser.  The generated
// component therefore cannot execute inside the secret-bearing Next.js server,
// while the model still owns every visual and behavioural product decision.
const ProductApp = dynamic(() => import("@/components/product/ProductApp"), {
  ssr: false,
  loading: () => null,
});

export function OmniaProductRuntime() {
  return <ProductApp />;
}
