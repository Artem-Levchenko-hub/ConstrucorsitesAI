import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { redirect } from "next/navigation";
import { AuthCard } from "@/components/auth/AuthCard";
import { OAuthButtons } from "@/components/auth/OAuthButtons";
import { RegisterForm } from "@/components/auth/RegisterForm";
import { listOAuthProviders } from "@/lib/oauth-login-server";

export default async function RegisterPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; source?: string; ref?: string }>;
}) {
  const { next, source, ref } = await searchParams;
  if (!next && !source && !ref) redirect("/max/register");

  const loginHref = next ? `/login?next=${encodeURIComponent(next)}` : "/login";

  const [t, providers] = await Promise.all([getTranslations("auth"), listOAuthProviders()]);

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
      <OAuthButtons providers={providers} next={next} />
    </AuthCard>
  );
}
