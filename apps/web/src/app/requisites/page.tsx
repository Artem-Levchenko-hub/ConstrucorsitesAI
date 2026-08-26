import type { Metadata } from "next";
import { ExternalLink } from "lucide-react";
import Link from "next/link";

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
        <dl className="overflow-hidden rounded-[12px] border border-[#2b2d32] bg-[#191b20]">
          <div className="grid gap-1 border-b border-[#2b2d32] p-5 sm:grid-cols-[180px_1fr] sm:gap-6">
            <dt className="text-sm text-[#828491]">Статус</dt>
            <dd className="font-medium text-white">
              Самозанятый, плательщик НПД
            </dd>
          </div>
          <div className="grid gap-1 p-5 sm:grid-cols-[180px_1fr] sm:gap-6">
            <dt className="text-sm text-[#828491]">ИНН</dt>
            <dd className="font-mono text-lg font-semibold tracking-[.04em] text-white">
              220504676540
            </dd>
          </div>
        </dl>
      </LegalSection>

      <LegalSection title="Услуги">
        <p>
          Доступ к онлайн-сервису Omnia, создание сайтов и веб-приложений,
          генерация программного кода, публикация и сопутствующие цифровые
          услуги. Актуальные пакеты стоят 490 ₽, 1 490 ₽ и 3 990 ₽. Подробный
          состав и сумма зачисления указаны на странице{" "}
          <Link className="font-medium text-[#6a95fa]" href="/pricing">
            «Тарифы»
          </Link>
          .
        </p>
      </LegalSection>

      <LegalSection title="Заказ, оплата и получение">
        <p>
          Покупатель выбирает пакет в личном кабинете и переходит на защищённую
          страницу ЮKassa. После подтверждения оплаты баланс активируется
          автоматически. Услуга предоставляется полностью онлайн: отдельная
          доставка не требуется, результат доступен в кабинете и по ссылке
          опубликованного проекта.
        </p>
      </LegalSection>

      <LegalSection title="Документы">
        <div className="flex flex-col items-start gap-2">
          <Link className="font-medium text-[#6a95fa]" href="/legal/offer">
            Публичная оферта
          </Link>
          <Link className="font-medium text-[#6a95fa]" href="/legal/refunds">
            Оплата и возвраты
          </Link>
          <Link className="font-medium text-[#6a95fa]" href="/legal/privacy">
            Политика конфиденциальности
          </Link>
        </div>
      </LegalSection>

      <LegalSection title="Контакты">
        <p>
          По вопросам сервиса, документов и платежей:{" "}
          <a
            className="font-medium text-[#6a95fa]"
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
          className="mt-4 inline-flex items-center gap-2 text-sm font-medium text-[#6a95fa]"
        >
          Проверить статус самозанятого на сайте ФНС
          <ExternalLink className="size-4" />
        </a>
      </LegalSection>
    </LegalPage>
  );
}
