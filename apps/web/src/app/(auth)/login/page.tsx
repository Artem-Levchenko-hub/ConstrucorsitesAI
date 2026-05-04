import Link from "next/link";
import { AuthCard } from "@/components/auth/AuthCard";
import { LoginForm } from "@/components/auth/LoginForm";

export default function LoginPage() {
  return (
    <AuthCard
      title="С возвращением"
      subtitle="Войдите, чтобы продолжить работу с проектами."
      footer={
        <>
          Нет аккаунта?{" "}
          <Link
            href="/register"
            className="text-accent hover:text-accent-hover transition"
          >
            Зарегистрируйтесь
          </Link>
        </>
      }
    >
      <LoginForm />
    </AuthCard>
  );
}
