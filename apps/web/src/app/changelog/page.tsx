import { CheckCircle2, GitCommit, Plug, Rocket } from "lucide-react";

import { PublicPageShell } from "@/components/marketing/PublicPageShell";

const releases = [
  { date: "30 июля 2026", Icon: Rocket, title: "Новый MaxStudio", text: "Пользовательский путь, лендинг и редактор перенесены в единую систему Figma." },
  { date: "29 июля 2026", Icon: Plug, title: "Integration Hub", text: "Добавлены управляемые подключения платежей, CRM, учёта и аналитики." },
  { date: "28 июля 2026", Icon: CheckCircle2, title: "Контроль готовности", text: "Публикация проверяет сборку, MAX-бота, HTTPS, webhook, URL и обязательные политики." },
];

export default function ChangelogPage() {
  return (
    <PublicPageShell
      eyebrow="Changelog"
      title="Что изменилось в продукте"
      lead="Короткая история значимых обновлений MaxStudio и сценария публикации MAX Mini Apps."
    >
      <div className="mx-auto max-w-[820px] space-y-5">
        {releases.map(({ date, Icon, title, text }) => (
          <article key={title} className="grid gap-5 rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-7 sm:grid-cols-[150px_1fr]">
            <div className="text-[12px] font-medium text-[#828491]">{date}</div>
            <div>
              <h2 className="flex items-center gap-3 text-[18px] font-semibold">
                <Icon className="h-5 w-5 text-[#4f81f7]" />
                {title}
              </h2>
              <p className="mt-3 text-[14px] leading-6 text-[#9fa1b1]">{text}</p>
            </div>
          </article>
        ))}
        <div className="flex items-center gap-3 px-2 pt-4 text-[12px] text-[#828491]">
          <GitCommit className="h-4 w-4" />
          Обновления публикуются после проверки production-сборки.
        </div>
      </div>
    </PublicPageShell>
  );
}
