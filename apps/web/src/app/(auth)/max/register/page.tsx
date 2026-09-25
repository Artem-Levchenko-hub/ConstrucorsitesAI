import Link from "next/link";
import { redirect } from "next/navigation";

import { OAuthButtons } from "@/components/auth/OAuthButtons";
import { BrandMark } from "@/components/marketing/BrandMark";
import { MaxRegisterForm } from "@/components/max/MaxRegisterForm";
import { MarketingEvents } from "@/components/marketing/MarketingEvents";
import { getSession } from "@/lib/auth-mock";
import { listOAuthProviders } from "@/lib/oauth-login-server";
import "@/components/max/max-studio.css";
import "@/components/marketing/max-public.css";

export default async function MaxRegisterPage() {
  const session = await getSession();
  if (session && !session.isAnon) redirect("/max/onboarding");
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
          <h1>Начать на Free</h1>
          {/* Оставлена одна фраза с фактом: бесплатна регистрация, а не генерация.
              Остальное человек и так понимает или прочтёт на витрине. */}
          <p>Без карты. Генерация расходует баланс, публикация зависит от тарифа.</p>
          <MaxRegisterForm />
          <div className="mt-6">
            <OAuthButtons providers={providers} next="/max" />
          </div>
        </section>
      </div>
    </main>
  );
}
