import { AuthCard } from "@/components/auth/AuthCard";
import { PasswordRecoveryForm } from "@/components/auth/PasswordRecoveryForm";

export default function ForgotPasswordPage() {
  return (
    <AuthCard
      title="Восстановление пароля"
      subtitle="Пришлём одноразовую ссылку на email аккаунта."
      footer={null}
    >
      <PasswordRecoveryForm />
    </AuthCard>
  );
}
