import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { AuthCard } from "@/components/auth/AuthCard";
import { LoginForm } from "@/components/auth/LoginForm";
import { OAuthButtons } from "@/components/auth/OAuthButtons";
import { oauthErrorMessage } from "@/lib/oauth-login";
import { listOAuthProviders } from "@/lib/oauth-login-server";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; oauth_error?: string }>;
}) {
  const { next, oauth_error: oauthError } = await searchParams;
  const maxFlow = next?.startsWith("/max") ?? false;
  const registerHref = maxFlow
    ? "/max/register"
    : next
      ? `/register?next=${encodeURIComponent(next)}`
      : "/register";

  const [t, providers] = await Promise.all([getTranslations("auth"), listOAuthProviders()]);
  const oauthMessage = oauthErrorMessage(oauthError);

  return (
    <AuthCard
      title={maxFlow ? "Вход в Yleum" : t("login.title")}
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
      {oauthMessage && (
        <p role="alert" className="rounded-[8px] bg-[#c63d35]/10 px-4 py-3 text-sm text-danger-fg">
          {oauthMessage}
        </p>
      )}
      <LoginForm next={next} />
      <OAuthButtons providers={providers} next={next} />
    </AuthCard>
  );
}
