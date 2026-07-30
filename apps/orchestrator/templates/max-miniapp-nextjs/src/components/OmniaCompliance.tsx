import { omniaMaxConfig as app } from "@/lib/omnia/max-config";

export function OmniaCompliance() {
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
      <span>{app.legal.age_rating}</span>
    </footer>
  );
}
