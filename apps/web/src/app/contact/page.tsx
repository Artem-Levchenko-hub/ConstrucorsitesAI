import { Building2, Mail, MessageSquare, Send } from "lucide-react";
import Link from "next/link";

import { PublicPageShell } from "@/components/marketing/PublicPageShell";

export default function ContactPage() {
  return (
    <PublicPageShell
      eyebrow="Контакты"
      title="Обсудим запуск вашего MAX-приложения"
      lead="Выберите подходящий канал: продуктовый вопрос, корпоративное внедрение или помощь с уже созданным проектом."
    >
      <div className="grid gap-5 lg:grid-cols-3">
        {[
          { Icon: MessageSquare, title: "Начать проект", text: "Создайте аккаунт и опишите приложение в коротком брифе.", href: "/max/register", label: "Открыть студию" },
          { Icon: Building2, title: "Корпоративный запуск", text: "Интеграции с внутренними системами, собственная VPS и сопровождение.", href: "mailto:sales@lead-generator.ru", label: "sales@lead-generator.ru" },
          { Icon: Mail, title: "Поддержка", text: "Вопросы по аккаунту, публикации, оплате и работающим приложениям.", href: "mailto:support@lead-generator.ru", label: "support@lead-generator.ru" },
        ].map(({ Icon, title, text, href, label }) => (
          <article key={title} className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-7">
            <Icon className="h-6 w-6 text-accent" />
            <h2 className="mt-5 text-[18px] font-semibold">{title}</h2>
            <p className="mt-2 min-h-12 text-[14px] leading-6 text-[#6d6962]">{text}</p>
            <Link href={href} className="mt-7 inline-flex items-center gap-2 text-[13px] font-semibold text-accent">
              {label}
              <Send className="h-4 w-4" />
            </Link>
          </article>
        ))}
      </div>
    </PublicPageShell>
  );
}
