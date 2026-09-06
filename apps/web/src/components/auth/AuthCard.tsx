import Link from "next/link";
import { ArrowLeft, Check, Eye, ShieldCheck } from "lucide-react";

import { BrandMark } from "@/components/marketing/BrandMark";
import "@/components/max/max-studio.css";
import "@/components/marketing/max-public.css";

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
    <main data-max-studio className="max-auth-shell">
      <header>
        <BrandMark />
        <Link href="/" className="max-public-link inline-flex items-center gap-2">
          <ArrowLeft className="size-3.5" />
          На главную
        </Link>
      </header>
      <section className="max-auth-layout">
        <div className="max-auth-context">
          <p className="max-public-kicker">MAX Studio</p>
          <h1>Продолжите с того места, где остановились</h1>
          <p>Создавайте и проверяйте приложение в одном рабочем пространстве. Доступы к MAX и внешним сервисам понадобятся только перед соответствующим запуском.</p>
          <ul>
            <li><Check className="size-4" /> Ответы брифа сохраняются в проекте</li>
            <li><Eye className="size-4" /> Изменения видны в мобильном предпросмотре</li>
            <li><ShieldCheck className="size-4" /> Секреты вводятся только в защищённых формах</li>
          </ul>
        </div>
        <div>
          <div className="max-auth-card">
            <p className="max-public-kicker">Аккаунт</p>
            <h2>{title}</h2>
            <p>{subtitle}</p>
            <div className="space-y-6">{children}</div>
          </div>
          {footer && <div className="max-auth-footer">{footer}</div>}
          <p className="mt-7 text-center text-[11px] leading-5 text-fg-tertiary">
            Защищённое соединение · сессиями можно управлять в профиле
          </p>
        </div>
      </section>
    </main>
  );
}
