import { omniaMaxConfig as app } from "@/lib/omnia/max-config";

export const metadata = { title: `Поддержка — ${app.app_name}` };

export default function SupportPage() {
  return (
    <main style={{ maxWidth: 680, margin: "0 auto", padding: "32px 20px 64px", lineHeight: 1.65 }}>
      <h1>Поддержка</h1>
      <p>Опишите проблему, ожидаемый результат и время, когда она возникла.</p>
      {app.support.email && (
        <p><strong>Email:</strong> <a href={`mailto:${app.support.email}`}>{app.support.email}</a></p>
      )}
      {app.support.phone && (
        <p><strong>Телефон:</strong> <a href={`tel:${app.support.phone}`}>{app.support.phone}</a></p>
      )}
      <p>{app.support.response_time}</p>
      <nav style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 28 }}>
        <a href="/legal/privacy">Конфиденциальность</a>
        <a href="/legal/terms">Условия использования</a>
      </nav>
    </main>
  );
}
