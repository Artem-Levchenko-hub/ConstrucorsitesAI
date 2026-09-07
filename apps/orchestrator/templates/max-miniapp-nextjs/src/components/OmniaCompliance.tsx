"use client";

import { useEffect, useState } from "react";
import { omniaMaxConfig as app } from "@/lib/omnia/max-config";
import { getOmniaAppConfig } from "@/lib/omnia/integration-client";

export function OmniaCompliance() {
  const [ageRating, setAgeRating] = useState(app.legal.age_rating);
  useEffect(() => {
    let active = true;
    let request = 0;
    const refresh = async () => {
      const current = ++request;
      try {
        const config = await getOmniaAppConfig();
        if (active && current === request) setAgeRating(config.legal.age_rating);
      } catch { /* Keep the last known marking while offline. */ }
    };
    const onVisible = () => { if (document.visibilityState === "visible") void refresh(); };
    void refresh();
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      active = false;
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);
  return (
    <footer
      style={{
        display: "flex",
        justifyContent: "center",
        gap: 16,
        flexWrap: "wrap",
        padding: "20px max(16px, env(safe-area-inset-left)) calc(20px + env(safe-area-inset-bottom))",
        fontSize: 12,
        opacity: 0.62,
      }}
    >
      <a href="/support">Поддержка</a>
      <a href="/legal/privacy">Конфиденциальность</a>
      <a href="/legal/terms">Условия</a>
      <span>{ageRating}</span>
    </footer>
  );
}
