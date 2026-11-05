import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { AuthCard } from "@/components/auth/AuthCard";
import { LoginForm } from "@/components/auth/LoginForm";
import { OAuthButtons } from "@/components/auth/OAuthButtons";
import { oauthErrorMessage } from "@/lib/oauth-login";
import { listOAuthProviders } from "@/lib/oauth-login-server";
import { maxStaffReturnPath } from "@/lib/max-staff-path";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; oauth_error?: string }>;
}) {
  const { next: requestedNext, oauth_error: oauthError } = await searchParams;
  const staffReturn = maxStaffReturnPath(requestedNext);
  const next = staffReturn ?? requestedNext;
  const maxFlow = next?.startsWith("/max") ?? false;
  const registerPath = maxFlow ? "/max/register" : "/register";
  const registerReturn = maxFlow ? staffReturn : next;
  const registerHref = registerReturn ? `${registerPath}?next=${encodeURIComponent(registerReturn)}` : registerPath;

  const [t, providers] = await Promise.all([getTranslations("auth"), listOAuthProviders()]);
  const oauthMessage = oauthErrorMessage(oauthError);

  return (
    <AuthCard
      title={maxFlow ? "Вход в Yleum" : t("login.title")}
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
        <p role="alert" className="rounded-[8px] bg-danger/10 px-4 py-3 text-sm text-danger-fg">
          {oauthMessage}
        </p>
      )}
      <LoginForm next={next} />
      <OAuthButtons providers={providers} next={next} />
    </AuthCard>
  );
}
