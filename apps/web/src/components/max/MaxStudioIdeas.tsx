"use client";

import { ArrowRight, Coffee, Scissors, ShoppingBag } from "lucide-react";

/**
 * Блок под списком приложений.
 *
 * На первом экране кабинета список занимал верхнюю треть, а две трети оставались
 * пустыми: ни подсказки «с чего начать», ни примеров. Для человека, который
 * зашёл второй раз, это выглядело как недоделанный продукт. Здесь — три готовые
 * идеи, каждая открывает мастер с уже заполненным описанием.
 */

export type MaxStudioIdea = {
  key: string;
  title: string;
  audience: string;
  idea: string;
  primaryAction: string;
  appType: "loyalty" | "catalog" | "booking";
  icon: typeof Coffee;
};

export const MAX_STUDIO_IDEAS: MaxStudioIdea[] = [
  {
    key: "coffee",
    title: "Кофейня",
    audience: "Постоянные гости кофейни",
    idea: "Меню с ценами, предзаказ к выдаче и баллы за каждую покупку. Гость выбирает напиток, оплачивает и забирает без очереди.",
    primaryAction: "Заказать кофе к выдаче",
    appType: "loyalty",
    icon: Coffee,
  },
  {
    key: "barber",
    title: "Барбершоп",
    audience: "Клиенты барбершопа",
    idea: "Онлайн-запись к мастеру: услуги с ценами и длительностью, свободное время на неделю вперёд, напоминание в боте.",
    primaryAction: "Записаться к мастеру",
    appType: "booking",
    icon: Scissors,
  },
  {
    key: "shop",
    title: "Магазин",
    audience: "Покупатели магазина",
    idea: "Каталог товаров с фото, размерами и наличием, избранное и оформление заказа с доставкой или самовывозом.",
    primaryAction: "Оформить заказ",
    appType: "catalog",
    icon: ShoppingBag,
  },
];

export function MaxStudioIdeas({ onPick }: { onPick: (idea: MaxStudioIdea) => void }) {
  return (
    <section className="max-studio-ideas" aria-labelledby="max-ideas-heading">
      <header>
        <h2 id="max-ideas-heading">С чего начать</h2>
        <p>Готовые описания: откроем мастер с заполненной идеей, останется поправить под себя.</p>
      </header>
      <div className="max-studio-ideas-grid">
        {MAX_STUDIO_IDEAS.map(idea => {
          const Icon = idea.icon;
          return (
            <button key={idea.key} type="button" className="max-studio-idea" onClick={() => onPick(idea)}>
              <span className="max-studio-idea-icon" aria-hidden="true"><Icon className="size-4" /></span>
              <span className="max-studio-idea-copy">
                <span className="max-studio-idea-title">{idea.title}</span>
                <span className="max-studio-idea-text">{idea.idea}</span>
              </span>
              <span className="max-studio-idea-go" aria-hidden="true">Взять за основу<ArrowRight className="size-3.5" /></span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
