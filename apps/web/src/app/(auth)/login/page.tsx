import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { AuthCard } from "@/components/auth/AuthCard";
import { LoginForm } from "@/components/auth/LoginForm";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const { next } = await searchParams;
  const maxFlow = next?.startsWith("/max") ?? false;
  const registerHref = maxFlow
    ? "/max/register"
    : next
      ? `/register?next=${encodeURIComponent(next)}`
      : "/register";

  const t = await getTranslations("auth");

  return (
    <AuthCard
      title={maxFlow ? "Вход в MAX Studio" : t("login.title")}
      subtitle={
        maxFlow
          ? "Продолжите настройку и запуск вашего MAX-приложения."
          : t("login.subtitle")
      }
      footer={
        <>
          {t("login.noAccount")}{" "}
          <Link
            href={registerHref}
            className="text-accent hover:text-accent-hover transition"
          >
            {t("login.registerLink")}
          </Link>
        </>
      }
    >
      <LoginForm next={next} />
    </AuthCard>
  );
}
