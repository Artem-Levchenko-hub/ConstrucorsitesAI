import Link from "next/link";
import { Bot, ChevronLeft } from "lucide-react";
import { redirect } from "next/navigation";

import { MaxRegisterForm } from "@/components/max/MaxRegisterForm";
import { getSession } from "@/lib/auth-mock";

export default async function MaxRegisterPage() {
  const session = await getSession();
  if (session && !session.isAnon) redirect("/max/onboarding");

  return (
    <main className="min-h-screen bg-[#121310] px-5 py-8 text-white">
      <div className="mx-auto flex max-w-5xl items-center justify-between">
        <Link href="/" className="flex items-center gap-2 text-sm text-white/55 hover:text-white">
          <ChevronLeft className="size-4" />
          На главную
        </Link>
        <Link href="/login?next=/max" className="text-sm text-[#7897f4] hover:text-white">
          Уже есть аккаунт
        </Link>
      </div>

      <div className="mx-auto grid max-w-5xl gap-12 py-14 lg:grid-cols-[0.85fr_1.15fr] lg:items-center">
        <section>
          <div className="mb-6 flex size-11 items-center justify-center border border-white/20 bg-[#315bd7]">
            <Bot className="size-5" />
          </div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[#7897f4]">
            MAX Studio
          </p>
          <h1 className="mt-4 text-4xl font-semibold tracking-[-0.04em] sm:text-5xl">
            Сначала владелец. Затем приложение.
          </h1>
          <p className="mt-5 max-w-md text-base leading-7 text-white/50">
            MAX принимает ботов от организаций, ИП и самозанятых. Мы один раз
            проверим владельца и сохраним реквизиты для следующих приложений.
          </p>
        </section>

        <section className="rounded-xl border border-white/10 bg-[#1a1b18] p-6 sm:p-8">
          <h2 className="text-2xl font-semibold">Создать аккаунт</h2>
          <p className="mt-2 mb-7 text-sm text-white/45">
            После регистрации подтвердите email и добавьте данные владельца.
          </p>
          <MaxRegisterForm />
        </section>
      </div>
    </main>
  );
}
