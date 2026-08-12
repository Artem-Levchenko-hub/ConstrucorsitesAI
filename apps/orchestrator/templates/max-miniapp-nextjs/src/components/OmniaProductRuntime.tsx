"use client";

import { type ComponentType, useEffect, useState } from "react";

type ProductComponent = ComponentType;

export function OmniaProductRuntime() {
  const [ProductApp, setProductApp] = useState<ProductComponent | null>(null);

  useEffect(() => {
    // A synchronous bundler require avoids the async Turbopack boundary that can
    // load ProductApp's chunk but never resolve it in a hot-reloaded dev runtime.
    // Keeping it inside an effect also prevents generated module code from being
    // evaluated by the secret-bearing Next.js server.
    const productModule = require("@/components/product/ProductApp") as {
      default: ProductComponent;
    };
    setProductApp(() => productModule.default);
  }, []);

  return (
    <div data-omnia-product-runtime="true" style={{ display: "contents" }}>
      {ProductApp ? <ProductApp /> : null}
    </div>
  );
}
