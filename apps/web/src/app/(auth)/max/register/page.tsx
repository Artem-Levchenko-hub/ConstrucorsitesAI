import Link from "next/link";
import { ArrowLeft, Building2, Check, ShieldCheck } from "lucide-react";
import { redirect } from "next/navigation";

import { BrandMark } from "@/components/marketing/BrandMark";
import { MaxRegisterForm } from "@/components/max/MaxRegisterForm";
import { getSession } from "@/lib/auth-mock";

export default async function MaxRegisterPage() {
  const session = await getSession();
  if (session && !session.isAnon) redirect("/max/onboarding");

  return (
    <main data-light-shell className="min-h-screen bg-[#f5f3ee] px-5 py-8 text-[#171716]">
      <header className="mx-auto flex max-w-[1120px] items-center justify-between">
        <div className="flex items-center gap-3">
          <BrandMark />
          <span className="h-5 w-px bg-[#d8d4cb]" />
          <span className="text-sm text-[#6d6962]">MAX Studio</span>
        </div>
        <Link href="/login?next=/max" className="text-sm text-accent hover:text-[#171716]">
          Уже есть аккаунт
        </Link>
      </header>

      <div className="mx-auto grid max-w-[1120px] gap-12 py-16 lg:grid-cols-[.85fr_1.15fr] lg:items-center lg:py-24">
        <section>
          <p className="omnia-kicker text-accent">Регистрация владельца</p>
          <h1 className="mt-5 max-w-[520px] text-[44px] font-semibold leading-[1.02] tracking-[-.05em] sm:text-[58px]">
            Сначала аккаунт. Затем приложение.
          </h1>
          <p className="mt-6 max-w-[500px] text-base leading-7 text-[#6d6962]">
            MAX принимает ботов от организаций, ИП и самозанятых. Omnia один раз
            проверит владельца и сохранит реквизиты для следующих приложений.
          </p>
          <div className="mt-10 space-y-4 border-t border-[#d8d4cb] pt-7 text-sm text-[#6d6962]">
            {[
              [Building2, "ООО, ИП или самозанятый"],
              [ShieldCheck, "Реквизиты и согласия сохраняются защищённо"],
              [Check, "Один бизнес — один бесплатный старт"],
            ].map(([Icon, text]) => {
              const ItemIcon = Icon as typeof Building2;
              return <p key={String(text)} className="flex items-center gap-3"><ItemIcon className="size-4 text-accent" />{String(text)}</p>;
            })}
          </div>
        </section>

        <section className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6 shadow-[0_24px_70px_rgba(23,23,22,.06)] sm:p-8">
          <h2 className="text-[28px] font-semibold tracking-[-.03em]">Создать аккаунт</h2>
          <p className="mb-7 mt-2 text-sm leading-6 text-[#6d6962]">
            После регистрации подтвердите email и добавьте данные владельца.
          </p>
          <MaxRegisterForm />
        </section>
      </div>
      <Link href="/" className="mx-auto flex w-fit items-center gap-2 text-xs text-[#8d887f]"><ArrowLeft className="size-3.5" />На главную</Link>
    </main>
  );
}
