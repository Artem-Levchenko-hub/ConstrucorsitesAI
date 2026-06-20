import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { AuthCard } from "@/components/auth/AuthCard";
import { RegisterForm } from "@/components/auth/RegisterForm";

export default async function RegisterPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; source?: string; ref?: string }>;
}) {
  const { next, source, ref } = await searchParams;
  const loginHref = next ? `/login?next=${encodeURIComponent(next)}` : "/login";

  const t = await getTranslations("auth");

  return (
    <AuthCard
      title={t("register.title")}
      subtitle={t("register.subtitle")}
      footer={
        <>
          {t("register.hasAccount")}{" "}
          <Link
            href={loginHref}
            className="text-accent hover:text-accent-hover transition"
          >
            {t("register.loginLink")}
          </Link>
        </>
      }
    >
      <RegisterForm next={next} source={source} referrerProjectId={ref} />
    </AuthCard>
  );
}
