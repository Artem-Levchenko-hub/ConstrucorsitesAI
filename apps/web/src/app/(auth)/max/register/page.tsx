import Link from "next/link";
import { Check, MailCheck, ShieldCheck } from "lucide-react";
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
        <section className="max-auth-context">
          <p className="max-public-kicker">Бесплатный тариф Free</p>
          <h1>
            Ваша идея начинается здесь.
          </h1>
          <p>
            Создайте аккаунт без оплаты и привязки карты. Подтвердите email,
            опишите идею — и переходите в своё рабочее пространство Yleum.
          </p>
          <ul>
            {[
              [MailCheck, "Подтвердите email и сразу создавайте проект"],
              [ShieldCheck, "Карта для регистрации не нужна"],
              [Check, "Подключение MAX — когда будете готовы к запуску"],
            ].map(([Icon, text]) => {
              const ItemIcon = Icon as typeof MailCheck;
              return <li key={String(text)}><ItemIcon className="size-4" />{String(text)}</li>;
            })}
          </ul>
        </section>

        <section className="max-auth-card">
          <h2>Начать на Free</h2>
          <p>
            Регистрация бесплатна. Генерация расходует баланс, а публикация
            зависит от тарифа. Условия доступны в аккаунте до оплаты.
          </p>
          <MaxRegisterForm />
          <div className="mt-6">
            <OAuthButtons providers={providers} next="/max" />
          </div>
        </section>
      </div>
    </main>
  );
}
