import type { Metadata } from "next";
import { AuthCard } from "@/components/auth/AuthCard";
import { EmailChangeConfirmation } from "./EmailChangeConfirmation";

export const metadata: Metadata = {
  title: "Подтверждение email — Yleum",
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};

export default function ConfirmEmailChangePage() {
  return <AuthCard title="Подтверждение email" subtitle="Безопасная смена адреса для входа" footer={null}>
    <EmailChangeConfirmation />
  </AuthCard>;
}
