import Link from "next/link";
import { AuthCard } from "@/components/auth/AuthCard";
import { RegisterForm } from "@/components/auth/RegisterForm";

export default async function RegisterPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const { next } = await searchParams;
  const loginHref = next ? `/login?next=${encodeURIComponent(next)}` : "/login";

  return (
    <AuthCard
      title="Создать аккаунт"
      subtitle="100 ₽ на счёт сразу — без карты, без подписки."
      footer={
        <>
          Уже есть аккаунт?{" "}
          <Link
            href={loginHref}
            className="text-accent hover:text-accent-hover transition"
          >
            Войдите
          </Link>
        </>
      }
    >
      <RegisterForm next={next} />
    </AuthCard>
  );
}
