import { LayoutGrid, MousePointerClick, Palette, Users } from "lucide-react";
import {
  MAX_APP_TYPES,
  MAX_FEATURE_INFO,
  MAX_STYLES,
  type MaxAppTypeId,
  type MaxFeature,
  type MaxStyleId,
} from "@/lib/max-brief";
import "./max-project-review.css";

type MaxProjectReviewProps = {
  name: string;
  idea: string;
  appType: MaxAppTypeId;
  audience: string;
  primaryAction: string;
  features: MaxFeature[];
  style: MaxStyleId;
  brandColors: string;
};

const later = "Уточним позже";

/** «1 функция», «3 функции», «5 функций» — иначе счётчик читается как ошибка. */
function featureCount(count: number): string {
  const tail = count % 100 >= 11 && count % 100 <= 14 ? 0 : count % 10;
  if (tail === 1) return `${count} функция`;
  if (tail >= 2 && tail <= 4) return `${count} функции`;
  return `${count} функций`;
}

export function MaxProjectReview({
  name,
  idea,
  appType,
  audience,
  primaryAction,
  features,
  style,
  brandColors,
}: MaxProjectReviewProps) {
  const appTypeOption = MAX_APP_TYPES.find(item => item.id === appType);
  const styleOption = MAX_STYLES.find(item => item.id === style);

  return (
    <section className="max-project-review" aria-label="Сводка проекта">
      <header className="max-project-review__identity">
        <p className="max-project-review__eyebrow">Соберём мини-приложение для MAX</p>
        <h3>{name}</h3>
        <p className="max-project-review__idea">
          <span className="max-project-review__idea-label">Что оно делает</span>
          {idea}
        </p>
      </header>

      <section className="max-project-review__action" aria-labelledby="max-review-primary-action">
        <span className="max-project-review__action-icon" aria-hidden="true">
          <MousePointerClick />
        </span>
        <div>
          <h4 id="max-review-primary-action">Главное действие</h4>
          <p className={primaryAction ? undefined : "max-project-review__placeholder"}>
            {primaryAction || later}
          </p>
          <small>Ради этого человек открывает приложение — с него начнём главный экран.</small>
        </div>
      </section>

      <section className="max-project-review__features" aria-labelledby="max-review-features">
        <h4 id="max-review-features">
          Что будет внутри
          <span className="max-project-review__count">{features.length ? featureCount(features.length) : "минимум по задаче"}</span>
        </h4>
        {features.length > 0 ? (
          <ul>
            {features.map(feature => (
              <li key={feature}>
                <span className="max-project-review__tick" aria-hidden="true">✓</span>
                <span className="max-project-review__feature-copy">
                  <span className="max-project-review__feature-label">{feature}</span>
                  <small>{MAX_FEATURE_INFO[feature].summary}</small>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="max-project-review__placeholder">
            Функции не выбраны — ИИ соберёт минимальный набор экранов под описание.
          </p>
        )}
      </section>

      <div className="max-project-review__facts">
        <section>
          <span className="max-project-review__fact-icon" aria-hidden="true"><LayoutGrid /></span>
          <h4>Тип приложения</h4>
          <p>{appTypeOption?.label}</p>
          <small>{appTypeOption?.description}</small>
        </section>
        <section>
          <span className="max-project-review__fact-icon" aria-hidden="true"><Users /></span>
          <h4>Для кого</h4>
          <p className={audience ? undefined : "max-project-review__placeholder"}>{audience || later}</p>
          <small>ИИ подберёт тексты и тон под этих людей.</small>
        </section>
        <section>
          <span className="max-project-review__fact-icon" aria-hidden="true"><Palette /></span>
          <h4>Оформление</h4>
          <p>{styleOption?.label}</p>
          <small>{brandColors ? `Цвета: ${brandColors}` : "Цвета подберём автоматически"}</small>
        </section>
      </div>

      <section className="max-project-review__next" aria-labelledby="max-review-next">
        <h4 id="max-review-next">Что произойдёт после кнопки «Создать проект»</h4>
        <ol>
          <li><span aria-hidden="true">1</span><span>Создадим проект и откроем редактор — описание уже будет внутри.</span></li>
          <li><span aria-hidden="true">2</span><span>ИИ соберёт первую рабочую версию приложения: экраны, данные, кнопки.</span></li>
          <li><span aria-hidden="true">3</span><span>Дальше правите словами в чате, а любую версию можно вернуть назад.</span></li>
        </ol>
      </section>
    </section>
  );
}
