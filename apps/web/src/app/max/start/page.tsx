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

import { MaxHelpHeader } from "@/components/max/MaxHelpHeader";
import { getMaxHelpBrandProps } from "@/lib/max-help-brand";
import "@/components/max/max-studio.css";
import "@/components/marketing/max-public.css";
import "./quick-start.css";

export const metadata: Metadata = {
  title: "Быстрый старт — Yleum",
  description: "Шесть последовательных этапов от идеи до рабочего приложения в MAX.",
  alternates: { canonical: "/max/start" },
};

const steps = [
  {
    number: "01",
    title: "Создайте проект",
    text: "Назовите приложение и одним предложением опишите, что сможет делать пользователь.",
    result: "Проект в редакторе, первая сборка уже запущена.",
    href: "/max/guide#project",
    icon: Smartphone,
  },
  {
    number: "02",
    title: "Проверьте сборку",
    text: "Откройте основные экраны в мобильном предпросмотре и попросите агента исправить найденное.",
    result: "Основной сценарий проверен в превью.",
    href: "/max/guide#builder",
    icon: Check,
  },
  {
    number: "03",
    title: "Заполните данные",
    text: "Добавьте сведения о продукте, владельце, поддержке, контенте и реальных функциях.",
    result: "Данные, контент и обязательные страницы заполнены.",
    href: "/max/guide#settings",
    icon: FileCheck2,
  },
  {
    number: "04",
    title: "Подключите безопасный вход MAX",
    text: "Создайте и промодерируйте бота в MAX Partner, затем один раз добавьте его секрет через защищённую форму Yleum.",
    result: "Безопасный вход MAX готов к запуску.",
    href: "/max/guide#max-bot",
    icon: Bot,
  },
  {
    number: "05",
    title: "Опубликуйте",
    text: "Запустите production-развёртывание и дождитесь постоянного HTTPS-адреса.",
    result: "Приложение доступно по постоянной HTTPS-ссылке.",
    href: "/max/guide#publish",
    icon: Rocket,
  },
  {
    number: "06",
    title: "Проверьте запуск в MAX",
    text: "Вставьте production URL в MAX Partner и откройте приложение в реальном клиенте. Webhook Yleum подготовит автоматически.",
    result: "Приложение открывается в MAX по кнопке запуска.",
    href: "/max/guide#acceptance",
    icon: ShieldCheck,
  },
] as const;

export default async function MaxQuickStartPage() {
  const brand = await getMaxHelpBrandProps("/max/product");
  return (
    <main data-max-studio className="max-docs max-help-page max-quick-start min-h-screen">
      <MaxHelpHeader brand={brand} />

      <section className="border-b border-[#2b2d32] bg-[#121519] text-white">
        <div className="mx-auto max-w-[1120px] px-5 py-14 sm:px-8 sm:py-20">
          <p className="omnia-kicker text-accent">Быстрый старт</p>
          <h1 className="mt-4 max-w-[760px] text-[42px] font-semibold leading-[1.04] tracking-[-.05em] sm:text-[62px]">
            От идеи до запуска за шесть этапов
          </h1>
          <p className="mt-5 max-w-[700px] text-sm leading-7 text-white/55 sm:text-base">
            На каждом этапе Yleum показывает одно следующее действие. Галочка
            появляется после серверной проверки, а не после перехода по странице.
          </p>
        </div>
      </section>

      <section className="mx-auto max-w-[1120px] px-5 py-10 sm:px-8 sm:py-14">
        <ol className="max-start-grid">
          {steps.map((step) => {
            const Icon = step.icon;
            return (
              <li key={step.number} className="max-start-card">
                <div className="max-start-card-heading">
                  <span className="max-start-icon">
                    <Icon size={19} aria-hidden="true" />
                  </span>
                  <span className="max-start-number">{step.number}<span> / 06</span></span>
                </div>
                <div className="max-start-copy">
                  <h2>{step.title}</h2>
                  <p>{step.text}</p>
                </div>
                <div className="max-start-card-footer">
                  <Link href={step.href} className="max-start-instruction" aria-label={`Подробная инструкция: ${step.title}`}>
                    Подробная инструкция
                    <ArrowRight size={17} aria-hidden="true" />
                  </Link>
                  <p className="max-start-outcome"><span className="max-start-outcome-marker" aria-hidden="true" /><span>{step.result}</span></p>
                </div>
              </li>
            );
          })}
        </ol>
      </section>
    </main>
  );
}
