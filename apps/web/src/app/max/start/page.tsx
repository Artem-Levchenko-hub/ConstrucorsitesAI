import type { Metadata } from "next";
import {
  ArrowRight,
  Bot,
  Check,
  FileCheck2,
  Rocket,
  ShieldCheck,
  Smartphone,
} from "lucide-react";
import Link from "next/link";

import { BrandMark } from "@/components/marketing/BrandMark";

export const metadata: Metadata = {
  title: "Быстрый старт — MAX Studio",
  description: "Шесть последовательных этапов от идеи до рабочего приложения в MAX.",
  alternates: { canonical: "/max/start" },
};

const steps = [
  {
    number: "01",
    title: "Создайте проект",
    text: "Назовите приложение и одним предложением опишите, что сможет делать пользователь.",
    result: "Проект открыт в редакторе, первая сборка запускается автоматически.",
    href: "/max/guide#project",
    icon: Smartphone,
  },
  {
    number: "02",
    title: "Проверьте сборку",
    text: "Откройте основные экраны в мобильном preview и попросите агента исправить найденное.",
    result: "Навигация, формы и главный сценарий работают без технических ошибок.",
    href: "/max/guide#builder",
    icon: Check,
  },
  {
    number: "03",
    title: "Заполните данные",
    text: "Добавьте сведения о продукте, владельце, поддержке, контенте и реальных функциях.",
    result: "Обязательные страницы и политики собраны из проверенных данных.",
    href: "/max/guide#settings",
    icon: FileCheck2,
  },
  {
    number: "04",
    title: "Подключите безопасный вход MAX",
    text: "Создайте и промодерируйте бота в MAX Partner, затем один раз добавьте его секрет через защищённую форму Omnia.",
    result: "Сервер может проверять подпись MAX и разделять данные пользователей.",
    href: "/max/guide#max-bot",
    icon: Bot,
  },
  {
    number: "05",
    title: "Опубликуйте",
    text: "Запустите production-развёртывание и дождитесь постоянного HTTPS-адреса.",
    result: "Контейнер работает, health-check пройден, URL доступен.",
    href: "/max/guide#publish",
    icon: Rocket,
  },
  {
    number: "06",
    title: "Проверьте запуск в MAX",
    text: "Вставьте production URL в MAX Partner и откройте приложение в реальном клиенте. Webhook Omnia подготовит автоматически.",
    result: "Кнопка запуска открывает приложение внутри MAX.",
    href: "/max/guide#acceptance",
    icon: ShieldCheck,
  },
] as const;

export default function MaxQuickStartPage() {
  return (
    <main data-product-shell className="min-h-screen bg-[#121519] text-white">
      <header className="border-b border-[#2b2d32] bg-[#191b20]">
        <div className="mx-auto flex min-h-16 max-w-[1120px] items-center justify-between gap-4 px-5 sm:px-8">
          <BrandMark href="/max/product" label="MAX Studio" />
          <div className="flex items-center gap-2">
            <Link href="/max/guide" className="hidden min-h-11 items-center px-3 text-xs text-[#9fa1b1] hover:text-white sm:inline-flex">
              Полное руководство
            </Link>
            <Link href="/login?next=/max" className="inline-flex min-h-11 items-center rounded-[8px] bg-[#4f81f7] px-4 text-xs font-semibold text-[#121519] hover:bg-[#6a95fa]">
              Открыть Studio
            </Link>
          </div>
        </div>
      </header>

      <section className="border-b border-[#2b2d32] bg-[#121519] text-white">
        <div className="mx-auto max-w-[1120px] px-5 py-14 sm:px-8 sm:py-20">
          <p className="omnia-kicker text-[#4f81f7]">Быстрый старт</p>
          <h1 className="mt-4 max-w-[760px] text-[42px] font-semibold leading-[1.04] tracking-[-.05em] sm:text-[62px]">
            От идеи до запуска за шесть этапов
          </h1>
          <p className="mt-5 max-w-[700px] text-sm leading-7 text-white/55 sm:text-base">
            На каждом этапе MAX Studio показывает одно следующее действие. Галочка
            появляется после серверной проверки, а не после перехода по странице.
          </p>
        </div>
      </section>

      <section className="mx-auto max-w-[1120px] px-5 py-10 sm:px-8 sm:py-14">
        <ol className="grid gap-4 md:grid-cols-2">
          {steps.map((step) => {
            const Icon = step.icon;
            return (
              <li key={step.number} className="rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-5 sm:p-7">
                <div className="flex items-center justify-between gap-4">
                  <span className="grid size-10 place-items-center rounded-[8px] bg-[#2b2d32] text-[#4f81f7]">
                    <Icon className="size-4" />
                  </span>
                  <span className="font-mono text-xs text-[#828491]">{step.number} / 06</span>
                </div>
                <h2 className="mt-6 text-xl font-semibold">{step.title}</h2>
                <p className="mt-2 text-sm leading-6 text-[#9fa1b1]">{step.text}</p>
                <div className="mt-5 rounded-[8px] border border-[#25272b] bg-[#121519] p-4">
                  <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#828491]">Результат этапа</p>
                  <p className="mt-2 text-xs leading-5 text-white">{step.result}</p>
                </div>
                <Link href={step.href} className="mt-5 inline-flex min-h-11 items-center gap-2 text-xs font-semibold text-[#6a95fa] hover:underline">
                  Подробная инструкция
                  <ArrowRight className="size-3.5" />
                </Link>
              </li>
            );
          })}
        </ol>
      </section>
    </main>
  );
}
