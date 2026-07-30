import type { Metadata } from "next";
import { ExternalLink } from "lucide-react";

import { LegalPage, LegalSection } from "@/components/legal/LegalPage";

export const metadata: Metadata = {
  title: "Реквизиты исполнителя — Omnia",
  description:
    "Реквизиты самозанятого исполнителя сервиса Omnia и конструктора сайтов.",
};

export default function RequisitesPage() {
  return (
    <LegalPage title="Реквизиты исполнителя">
      <LegalSection title="Статус">
        <p>
          Исполнитель применяет специальный налоговый режим «Налог на
          профессиональный доход» и оказывает услуги как самозанятый.
        </p>
      </LegalSection>

      <LegalSection title="Идентификационные данные">
        <dl className="overflow-hidden rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7]">
          <div className="grid gap-1 border-b border-[#e5e1d8] p-5 sm:grid-cols-[180px_1fr] sm:gap-6">
            <dt className="text-sm text-[#8d887f]">Статус</dt>
            <dd className="font-medium text-[#171716]">
              Самозанятый, плательщик НПД
            </dd>
          </div>
          <div className="grid gap-1 p-5 sm:grid-cols-[180px_1fr] sm:gap-6">
            <dt className="text-sm text-[#8d887f]">ИНН</dt>
            <dd className="font-mono text-lg font-semibold tracking-[.04em] text-[#171716]">
              220504676540
            </dd>
          </div>
        </dl>
      </LegalSection>

      <LegalSection title="Услуги">
        <p>
          Доступ к онлайн-сервису Omnia, создание сайтов и веб-приложений,
          генерация программного кода, публикация и сопутствующие цифровые
          услуги. Условия использования размещены в публичном разделе сайта.
        </p>
      </LegalSection>

      <LegalSection title="Контакты">
        <p>
          По вопросам сервиса, документов и платежей:{" "}
          <a
            className="font-medium text-[#c84528]"
            href="mailto:support@lead-generator.ru"
          >
            support@lead-generator.ru
          </a>
          .
        </p>
        <a
          href="https://npd.nalog.ru/check-status/"
          target="_blank"
          rel="noreferrer"
          className="mt-4 inline-flex items-center gap-2 text-sm font-medium text-[#c84528]"
        >
          Проверить статус самозанятого на сайте ФНС
          <ExternalLink className="size-4" />
        </a>
      </LegalSection>
    </LegalPage>
  );
}
