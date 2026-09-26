"use client";

import { ArrowRight, Bot, Check, FileCheck2, Hammer, Rocket } from "lucide-react";
import Link from "next/link";
import type { MaxReadiness } from "@/lib/api/types";
import { getMaxJourneyItemHref } from "@/lib/max-journey";

export const PUBLICATION_REQUIREMENTS = [
  {
    id: "build",
    title: "Приложение собрано",
    pending: "собрать приложение",
    description: "Рабочая версия готова в редакторе.",
    action: "В редактор",
    icon: Hammer,
  },
  {
    id: "legal",
    title: "Документы подтверждены",
    pending: "подтвердить документы",
    description: "Политика и условия приложения приняты во вкладке «Политики».",
    action: "Подтвердить",
    icon: FileCheck2,
  },
  {
    id: "bot",
    title: "Бот MAX подключён",
    pending: "подключить бота MAX",
    description: "Создайте бота в MAX и вставьте его токен — покажем, где взять.",
    action: "Подключить",
    icon: Bot,
  },
] as const;

/** Требование → этап маршрута, к которому оно относится. */
const STAGE_OF: Record<string, string> = { build: "build", legal: "app", bot: "bot" };

/** «Остался 1 шаг», «Осталось 2 шага» — счёт словами, а не голой цифрой. */
function stepsLeft(count: number): string {
  const tail = count % 100 >= 11 && count % 100 <= 14 ? 0 : count % 10;
  if (tail === 1) return `остался ${count} шаг`;
  if (tail >= 2 && tail <= 4) return `осталось ${count} шага`;
  return `осталось ${count} шагов`;
}

export function MaxPublicationRequirements({ projectId, items, status, promotedId = null }: {
  projectId: string;
  items: MaxReadiness["items"];
  status: "ready" | "loading" | "error";
  /** Шаг, который уже вынесен кнопкой выше: в маршруте он без второй кнопки. */
  promotedId?: string | null;
}) {
  const ready = status === "ready";
  const remaining = ready
    ? PUBLICATION_REQUIREMENTS.filter(requirement => !items.find(item => item.id === requirement.id)?.done)
    : [];
  const addressDone = ready && items.find(item => item.id === "max_url")?.done === true;
  const heading = !ready
    ? "Что нужно до публикации"
    : remaining.length === 0
      ? "Всё готово к публикации"
      : `До публикации ${stepsLeft(remaining.length)} из ${PUBLICATION_REQUIREMENTS.length}`;
  const lead = !ready
    ? status === "loading" ? "Проверяем, что уже сделано." : "Статус шагов появится, когда сервер ответит."
    : remaining.length === 0
      ? "Можно публиковать: дальше дадим постоянный адрес приложения."
      // Названия шагов написаны в прошедшем времени («Документы подтверждены»),
      // поэтому в строке «осталось» берём отдельную форму дела, а не заголовок.
      : `Осталось: ${remaining.map(requirement => requirement.pending).join(", ")}.`;

  return <div className="max-publication-requirements">
    <section aria-label="Путь до публикации">
      <header className="max-requirement-heading">
        <div>
          <h3>{heading}</h3>
          <p>{lead}</p>
        </div>
        {ready && <span className="max-requirement-progress" role="img"
          aria-label={`Готово ${PUBLICATION_REQUIREMENTS.length - remaining.length} из ${PUBLICATION_REQUIREMENTS.length}`}>
          {PUBLICATION_REQUIREMENTS.map((requirement, index) => (
            <span key={requirement.id} data-done={index < PUBLICATION_REQUIREMENTS.length - remaining.length} />
          ))}
        </span>}
      </header>
      <ul>{PUBLICATION_REQUIREMENTS.map(requirement => {
        const item = ready ? items.find(entry => entry.id === requirement.id) : undefined;
        const state = !item ? "unknown" : item.done ? "done" : "missing";
        const Icon = requirement.icon;
        const promoted = promotedId !== null && STAGE_OF[requirement.id] === promotedId;
        return <li key={requirement.id} data-requirement={requirement.id} data-state={state} data-promoted={promoted || undefined}>
          <span className="max-requirement-icon" aria-hidden="true">{item?.done ? <Check /> : <Icon />}</span>
          <div><h4>{requirement.title}</h4><p>{requirement.description}</p></div>
          {state === "done" ? <span className="max-requirement-status">Готово</span>
            : state === "unknown" ? <span className="max-requirement-status">{status === "loading" ? "Проверяем…" : "Не проверено"}</span>
            : promoted ? <span className="max-requirement-status max-requirement-now">Делаем сейчас</span>
            : <Link className="max-requirement-action" href={getMaxJourneyItemHref(projectId, requirement.id)}>{requirement.action}<ArrowRight aria-hidden="true" /></Link>}
        </li>;
      })}
        {/* Шаг после публикации живёт в том же маршруте: владелец должен
            заранее видеть, что его ждёт, но не принимать за требование. */}
        <li data-requirement="max_url" data-state={addressDone ? "done" : "later"}>
          <span className="max-requirement-icon" aria-hidden="true">{addressDone ? <Check /> : <Rocket />}</span>
          <div>
            <h4>После публикации: вставить адрес в кабинет MAX</h4>
            <p>Дадим готовый адрес приложения и покажем, куда его вставить.</p>
          </div>
          {addressDone ? <span className="max-requirement-status">Адрес подтверждён</span>
            : <Link className="max-requirement-action" href={`/max/${projectId}?panel=max`}>Как это сделать<ArrowRight aria-hidden="true" /></Link>}
        </li>
      </ul>
    </section>
    <details className="max-publication-optional">
      <summary>Что можно не заполнять</summary>
      <p>Дополнительное описание, аудитория, оформление и каталог публикацию не блокируют. Реквизиты компании Yleum не запрашивает: бизнес проверяет сам MAX при выдаче токена бота. Название и описание уже сохранены из вашей идеи — заполнять их заново не нужно.</p>
      <p>Продажи, пользовательский контент и рассылки отмечайте в «Политиках» только если они используются. Сервисы и свой сервер — по необходимости.</p>
    </details>
  </div>;
}
