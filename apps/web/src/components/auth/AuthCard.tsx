import Link from "next/link";
import { ArrowLeft } from "lucide-react";

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
    <main data-product-shell className="relative min-h-svh bg-[#121519] px-5 py-8 text-white">
      <header className="mx-auto flex max-w-[1320px] items-center justify-between">
        <BrandMark />
        <Link href="/" className="inline-flex items-center gap-2 text-xs text-[#9fa1b1] hover:text-white">
          <ArrowLeft className="size-3.5" />
          На главную
        </Link>
      </header>
      <section className="mx-auto flex min-h-[calc(100svh-96px)] max-w-[420px] items-center py-12">
        <div className="w-full">
          <div className="rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-6 shadow-[0_18px_50px_rgba(23,23,22,.06)] sm:p-8">
            <p className="omnia-kicker text-[#4f81f7]">Omnia / аккаунт</p>
            <h1 className="mt-4 text-[30px] font-semibold leading-tight tracking-[-.035em]">{title}</h1>
            <p className="mt-2 text-sm leading-6 text-[#9fa1b1]">{subtitle}</p>
            <div className="mt-7 space-y-6">{children}</div>
          </div>
          {footer && <div className="mt-6 text-center text-sm text-[#9fa1b1]">{footer}</div>}
          <p className="mt-8 text-center text-[11px] leading-5 text-[#828491]">
            Защищённое соединение · сессиями можно управлять в профиле
          </p>
        </div>
      </section>
    </main>
  );
}
