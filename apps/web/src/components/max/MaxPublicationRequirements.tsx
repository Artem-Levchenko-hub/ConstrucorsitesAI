"use client";

import { Check, Circle, ArrowRight } from "lucide-react";
import Link from "next/link";
import type { MaxReadiness } from "@/lib/api/types";
import { getMaxJourneyItemHref } from "@/lib/max-journey";

export const PUBLICATION_REQUIREMENTS = [
  { id: "build", title: "Готовая версия приложения", description: "Соберите приложение в редакторе.", action: "В редактор" },
  { id: "business", title: "Владелец и email поддержки", description: "ФИО или название владельца и действующий email — два поля во вкладке «Владелец».", action: "Заполнить" },
  { id: "legal", title: "Подтверждение данных", description: "Проверьте документы и отметьте «Подтверждаю корректность данных владельца» во вкладке «Политики».", action: "Подтвердить" },
  { id: "bot", title: "Подключённый бот MAX", description: "Создайте бота в MAX и подключите его токен.", action: "Подключить" },
] as const;

export function MaxPublicationRequirements({ projectId, items, status }: {
  projectId: string;
  items: MaxReadiness["items"];
  status: "ready" | "loading" | "error";
}) {
  return <div className="max-publication-requirements">
    <section aria-label="Обязательно до публикации">
      <h3>Обязательно до публикации</h3>
      <ul>{PUBLICATION_REQUIREMENTS.map(requirement => {
        const item = status === "ready" ? items.find(item => item.id === requirement.id) : undefined;
        const state = !item ? "unknown" : item.done ? "done" : "missing";
        return <li key={requirement.id} data-requirement={requirement.id} data-state={state}>
          <span className="max-requirement-icon" aria-hidden="true">{item?.done ? <Check /> : <Circle />}</span>
          <div><h4>{requirement.title}</h4><p>{requirement.description}</p></div>
          {state === "done" ? <span className="max-requirement-status">Готово</span>
            : state === "unknown" ? <span className="max-requirement-status">{status === "loading" ? "Проверяем…" : "Не проверено"}</span>
            : <Link className="max-requirement-action" href={getMaxJourneyItemHref(projectId, requirement.id)}>{requirement.action}<ArrowRight aria-hidden="true" /></Link>}
        </li>;
      })}</ul>
    </section>
    <section className="max-publication-optional" aria-label="Можно заполнить позже">
      <h3>Можно заполнить позже</h3>
      <p>Дополнительное описание, аудитория, оформление, каталог, ИНН, ОГРН, адрес и телефон не блокируют публикацию в Studio. Название и описание уже сохранены из вашей идеи — заполнять их заново не нужно.</p>
      <p>Продажи, пользовательский контент и рассылки отмечайте в «Политиках» только если они используются. Сервисы и свой сервер — по необходимости.</p>
    </section>
    <section className="max-publication-after" aria-label="После публикации">
      <div><h3>После публикации</h3><p>Вставьте полученный адрес приложения в кабинет MAX и подтвердите это в Studio. До публикации адрес не требуется.</p></div>
      {status === "ready" && items.find(item => item.id === "max_url")?.done
        ? <span className="max-requirement-status">Адрес подтверждён</span>
        : <Link className="max-requirement-action" href={`/max/${projectId}?panel=max`}>Как это сделать<ArrowRight aria-hidden="true" /></Link>}
    </section>
  </div>;
}
