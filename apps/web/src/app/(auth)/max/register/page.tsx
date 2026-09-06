import Link from "next/link";
import { Check, MailCheck, ShieldCheck } from "lucide-react";
import { redirect } from "next/navigation";

import { BrandMark } from "@/components/marketing/BrandMark";
import { MaxRegisterForm } from "@/components/max/MaxRegisterForm";
import { getSession } from "@/lib/auth-mock";
import "@/components/max/max-studio.css";
import "@/components/marketing/max-public.css";

export default async function MaxRegisterPage() {
  const session = await getSession();
  if (session && !session.isAnon) redirect("/max/onboarding");

  return (
    <main data-max-studio className="max-auth-shell">
      <header>
        <div className="flex items-center gap-3">
          <BrandMark />
          <span className="h-5 w-px bg-border-default" />
          <span className="text-sm text-fg-secondary">MAX Studio</span>
        </div>
        <Link href="/login?next=/max" className="max-public-link">
          Уже есть аккаунт
        </Link>
      </header>

      <div className="max-auth-layout">
        <section className="max-auth-context">
          <p className="max-public-kicker">Регистрация владельца</p>
          <h1>
            Сначала аккаунт. Затем приложение.
          </h1>
          <p>
            Для первого проекта нужен только рабочий email. Бизнес-профиль и
            модерация бота проходят в MAX Partner; Omnia не просит повторно ИНН и ОГРН,
            а секрет подключается один раз только перед production.
          </p>
          <ul>
            {[
              [MailCheck, "Подтвердите email и сразу создавайте проект"],
              [ShieldCheck, "Без ИНН, ОГРН и секрета до первого результата"],
              [Check, "MAX и платежи подключаются только перед запуском"],
            ].map(([Icon, text]) => {
              const ItemIcon = Icon as typeof MailCheck;
              return <li key={String(text)}><ItemIcon className="size-4" />{String(text)}</li>;
            })}
          </ul>
        </section>

        <section className="max-auth-card">
          <h2>Создать аккаунт</h2>
          <p>
            После регистрации подтвердите email — и сразу переходите к созданию проекта.
          </p>
          <MaxRegisterForm />
        </section>
      </div>
    </main>
  );
}
