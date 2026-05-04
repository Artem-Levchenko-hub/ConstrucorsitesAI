import Link from "next/link";
import { AuthCard } from "@/components/auth/AuthCard";
import { RegisterForm } from "@/components/auth/RegisterForm";

export default function RegisterPage() {
  return (
    <AuthCard
      title="Создать аккаунт"
      subtitle="100 ₽ на счёт сразу — без карты, без подписки."
      footer={
        <>
          Уже есть аккаунт?{" "}
          <Link
            href="/login"
            className="text-accent hover:text-accent-hover transition"
          >
            Войдите
          </Link>
        </>
      }
    >
      <RegisterForm />
    </AuthCard>
  );
}
