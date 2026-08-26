import Link from "next/link";
import { ArrowLeft, Check, MailCheck, ShieldCheck } from "lucide-react";
import { redirect } from "next/navigation";

import { BrandMark } from "@/components/marketing/BrandMark";
import { MaxRegisterForm } from "@/components/max/MaxRegisterForm";
import { getSession } from "@/lib/auth-mock";

export default async function MaxRegisterPage() {
  const session = await getSession();
  if (session && !session.isAnon) redirect("/max/onboarding");

  return (
    <main data-product-shell className="min-h-screen bg-[#121519] px-5 py-8 text-white">
      <header className="mx-auto flex max-w-[1120px] items-center justify-between">
        <div className="flex items-center gap-3">
          <BrandMark />
          <span className="h-5 w-px bg-[#2b2d32]" />
          <span className="text-sm text-[#9fa1b1]">MAX Studio</span>
        </div>
        <Link href="/login?next=/max" className="text-sm text-[#6a95fa] hover:text-white">
          Уже есть аккаунт
        </Link>
      </header>

      <div className="mx-auto grid max-w-[1120px] gap-12 py-16 lg:grid-cols-[.85fr_1.15fr] lg:items-center lg:py-24">
        <section>
          <p className="omnia-kicker text-[#4f81f7]">Регистрация владельца</p>
          <h1 className="mt-5 max-w-[520px] text-[44px] font-semibold leading-[1.02] tracking-[-.05em] sm:text-[58px]">
            Сначала аккаунт. Затем приложение.
          </h1>
          <p className="mt-6 max-w-[500px] text-base leading-7 text-[#9fa1b1]">
            Для первого проекта нужен только рабочий email. Бизнес-профиль и
            модерация бота проходят в MAX Partner; Omnia не просит повторно ИНН и ОГРН,
            а секрет подключается один раз только перед production.
          </p>
          <div className="mt-10 space-y-4 border-t border-[#2b2d32] pt-7 text-sm text-[#9fa1b1]">
            {[
              [MailCheck, "Подтвердите email и сразу создавайте проект"],
              [ShieldCheck, "Без ИНН, ОГРН и секрета до первого результата"],
              [Check, "MAX и платежи подключаются только перед запуском"],
            ].map(([Icon, text]) => {
              const ItemIcon = Icon as typeof MailCheck;
              return <p key={String(text)} className="flex items-center gap-3"><ItemIcon className="size-4 text-[#4f81f7]" />{String(text)}</p>;
            })}
          </div>
        </section>

        <section className="rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-6 shadow-[0_24px_70px_rgba(23,23,22,.06)] sm:p-8">
          <h2 className="text-[28px] font-semibold tracking-[-.03em]">Создать аккаунт</h2>
          <p className="mb-7 mt-2 text-sm leading-6 text-[#9fa1b1]">
            После регистрации подтвердите email — и сразу переходите к созданию проекта.
          </p>
          <MaxRegisterForm />
        </section>
      </div>
      <Link href="/" className="mx-auto flex w-fit items-center gap-2 text-xs text-[#828491]"><ArrowLeft className="size-3.5" />На главную</Link>
    </main>
  );
}
