import { AuthCard } from "@/components/auth/AuthCard";
import { PasswordResetForm } from "@/components/auth/PasswordResetForm";

export default async function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;
  return (
    <AuthCard
      title="Новый пароль"
      subtitle="После сохранения вход на остальных устройствах будет завершён."
      footer={null}
    >
      <PasswordResetForm token={token ?? ""} />
    </AuthCard>
  );
}
