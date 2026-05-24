import Link from "next/link";
import { AuthCard } from "@/components/auth/AuthCard";
import { LoginForm } from "@/components/auth/LoginForm";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const { next } = await searchParams;
  const registerHref = next
    ? `/register?next=${encodeURIComponent(next)}`
    : "/register";

  return (
    <AuthCard
      title="С возвращением"
      subtitle="Войдите, чтобы продолжить работу с проектами."
      footer={
        <>
          Нет аккаунта?{" "}
          <Link
            href={registerHref}
            className="text-accent hover:text-accent-hover transition"
          >
            Зарегистрируйтесь
          </Link>
        </>
      }
    >
      <LoginForm next={next} />
    </AuthCard>
  );
}
