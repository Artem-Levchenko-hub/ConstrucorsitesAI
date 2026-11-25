import type { Metadata } from "next";

import { AuthCard } from "@/components/auth/AuthCard";
import { MonitoringLoginForm } from "@/components/auth/MonitoringLoginForm";

export const metadata: Metadata = {
  title: "Вход в мониторинг",
  robots: { index: false, follow: false },
};

export default function MonitoringLoginPage() {
  return (
    <AuthCard
      title="Вход в мониторинг"
      subtitle="Войдите под отдельным аккаунтом наблюдателя."
      footer={null}
    >
      <MonitoringLoginForm />
    </AuthCard>
  );
}
