import Link from "next/link";

import { OAuthButtons } from "@/components/auth/OAuthButtons";
import { BrandMark } from "@/components/marketing/BrandMark";
import { MaxRegisterForm } from "@/components/max/MaxRegisterForm";
import { MarketingEvents } from "@/components/marketing/MarketingEvents";
import { getSession } from "@/lib/auth-mock";
import { listOAuthProviders } from "@/lib/oauth-login-server";
import { safeReturnPath } from "@/lib/auth-return";
import "@/components/max/max-studio.css";
import "@/components/marketing/max-public.css";

export default async function MaxRegisterPage({ searchParams }: { searchParams?: Promise<{ next?: string }> }) {
  const next = safeReturnPath((await searchParams)?.next) ?? "/max";
  const session = await getSession();
  const providers = await listOAuthProviders();

  return (
    <main data-max-studio className="max-auth-shell">
      <MarketingEvents page="registration" />
      <header>
        <div className="flex items-center gap-3">
          <BrandMark />
          <span className="h-5 w-px bg-border-default" />
          <span className="text-sm text-fg-secondary">Yleum</span>
        </div>
        <Link href="/login?next=/max" className="max-public-link">
          Уже есть аккаунт
        </Link>
      </header>

      <div className="max-auth-layout">
        <section className="max-auth-card">
          <h1>Регистрация</h1>
          <div className="mt-6">
            <MaxRegisterForm next={next} existingSession={Boolean(session && !session.isAnon)}>
              <div className="mt-6"><OAuthButtons providers={providers} next={next} /></div>
            </MaxRegisterForm>
          </div>
        </section>
      </div>
    </main>
  );
}
