import type { Metadata } from "next";
import { ExternalLink } from "lucide-react";
import Link from "next/link";

import { LegalPage, LegalSection } from "@/components/legal/LegalPage";

export const metadata: Metadata = {
  title: "Реквизиты исполнителя — Omnia",
  description:
    "Реквизиты самозанятого исполнителя сервиса Omnia MAX Studio.",
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
        <dl className="overflow-hidden rounded-[12px] border border-border-default bg-surface">
          <div className="grid gap-1 border-b border-border-default p-5 sm:grid-cols-[180px_1fr] sm:gap-6">
            <dt className="text-sm text-fg-tertiary">Статус</dt>
            <dd className="font-medium text-fg-primary">
              Самозанятый, плательщик НПД
            </dd>
          </div>
          <div className="grid gap-1 p-5 sm:grid-cols-[180px_1fr] sm:gap-6">
            <dt className="text-sm text-fg-tertiary">ИНН</dt>
            <dd className="font-mono text-lg font-semibold tracking-[.04em] text-fg-primary">
              220504676540
            </dd>
          </div>
        </dl>
      </LegalSection>

      <LegalSection title="Услуги">
        <p>
          Доступ к онлайн-сервису Omnia MAX Studio, создание MAX-приложений,
          генерация программного кода, публикация и сопутствующие цифровые
          услуги. Актуальные условия, состав и сумма показываются из действующей
          конфигурации в личном кабинете до оплаты. Общий порядок описан на странице{" "}
          <Link className="font-medium text-accent-secondary" href="/pricing">
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
          <Link className="font-medium text-accent-secondary" href="/legal/offer">
            Публичная оферта
          </Link>
          <Link className="font-medium text-accent-secondary" href="/legal/refunds">
            Оплата и возвраты
          </Link>
          <Link className="font-medium text-accent-secondary" href="/legal/privacy">
            Политика конфиденциальности
          </Link>
        </div>
      </LegalSection>

      <LegalSection title="Контакты">
        <p>
          По вопросам сервиса, документов и платежей:{" "}
          <a
            className="font-medium text-accent-secondary"
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
          className="mt-4 inline-flex items-center gap-2 text-sm font-medium text-accent-secondary"
        >
          Проверить статус самозанятого на сайте ФНС
          <ExternalLink className="size-4" />
        </a>
      </LegalSection>
    </LegalPage>
  );
}
