import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { BrandMark } from "@/components/marketing/BrandMark";
import "@/components/max/max-studio.css";
import "@/components/marketing/max-public.css";

/**
 * Оболочка страниц входа, регистрации и восстановления пароля.
 *
 * Здесь намеренно нет рекламной колонки и объяснений: человек, открывший форму
 * входа, уже знает, что такое вход. Подзаголовок необязателен и уместен только
 * там, где сообщает то, чего не угадать, — срок жизни ссылки или последствие
 * смены пароля.
 */
export function AuthCard({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footer: React.ReactNode;
}) {
  return (
    <main data-max-studio className="max-auth-shell">
      <header>
        <BrandMark />
        <Link href="/" className="max-public-link inline-flex items-center gap-2">
          <ArrowLeft className="size-3.5" />
          На главную
        </Link>
      </header>
      <section className="max-auth-layout">
        <div className="max-auth-card">
          <h1>{title}</h1>
          {subtitle && <p>{subtitle}</p>}
          <div className="space-y-6">{children}</div>
        </div>
        {footer && <div className="max-auth-footer">{footer}</div>}
      </section>
    </main>
  );
}
