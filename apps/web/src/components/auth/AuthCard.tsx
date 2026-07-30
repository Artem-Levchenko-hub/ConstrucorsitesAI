import Link from "next/link";
import { ArrowLeft, Check, ShieldCheck } from "lucide-react";

import { BrandMark } from "@/components/marketing/BrandMark";

export function AuthCard({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  footer: React.ReactNode;
}) {
  return (
    <main className="studio-grid min-h-svh bg-[#080a10] text-white">
      <div className="mx-auto grid min-h-svh max-w-[1280px] lg:grid-cols-[0.9fr_1.1fr]">
        <section className="hidden flex-col border-r border-[#1e243f] p-10 lg:flex xl:p-14">
          <BrandMark inverse label="MaxStudio" />
          <div className="my-auto max-w-lg">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-400">
              MAX Studio
            </p>
            <h2 className="font-display mt-5 text-5xl font-semibold leading-[1.02] tracking-[-0.045em]">
              От идеи до работающего приложения — в одном кабинете.
            </h2>
            <div className="mt-9 space-y-4">
              {[
                "Код, backend и мобильное превью",
                "MAX-бот, интеграции и HTTPS",
                "Защищённые данные и история версий",
              ].map((item) => (
                <div key={item} className="flex items-center gap-3 text-sm text-slate-400">
                  <span className="grid h-6 w-6 place-items-center rounded-full bg-emerald-400/10 text-emerald-400">
                    <Check className="h-3.5 w-3.5" />
                  </span>
                  {item}
                </div>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-600">
            <ShieldCheck className="h-4 w-4" />
            Данные передаются по защищённому соединению
          </div>
        </section>

        <section className="flex min-h-svh items-center justify-center px-5 py-10 sm:px-8">
          <div className="w-full max-w-md">
            <div className="mb-10 flex items-center justify-between lg:hidden">
              <BrandMark inverse label="MaxStudio" />
              <Link href="/" className="text-slate-500 hover:text-white" aria-label="На главную">
                <ArrowLeft className="h-5 w-5" />
              </Link>
            </div>
            <div className="rounded-2xl border border-[#263150] bg-[#0f121f] p-6 shadow-[0_24px_80px_rgba(0,0,0,0.3)] sm:p-8">
              <div className="space-y-2">
                <h1 className="font-display text-3xl font-semibold tracking-[-0.035em]">{title}</h1>
                <p className="text-sm leading-6 text-slate-400">{subtitle}</p>
              </div>

              <div className="mt-7 space-y-6">{children}</div>
            </div>

            <div className="mt-6 text-center text-sm text-slate-500">{footer}</div>
            <div className="mt-8 hidden justify-center lg:flex">
              <Link href="/" className="inline-flex items-center gap-2 text-xs text-slate-600 transition hover:text-slate-300">
                <ArrowLeft className="h-3.5 w-3.5" />
                На главную
              </Link>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
