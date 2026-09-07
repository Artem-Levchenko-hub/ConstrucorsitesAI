import { MousePointerClick } from "lucide-react";
import {
  MAX_APP_TYPES,
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
  const appTypeLabel = MAX_APP_TYPES.find(item => item.id === appType)?.label;
  const styleLabel = MAX_STYLES.find(item => item.id === style)?.label;

  return (
    <section className="max-project-review" aria-label="Сводка проекта">
      <header className="max-project-review__identity">
        <p className="max-project-review__eyebrow">Название</p>
        <h3>{name}</h3>
        <p className="max-project-review__idea">{idea}</p>
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
        </div>
      </section>

      <div className="max-project-review__supporting">
        <section className="max-project-review__features" aria-labelledby="max-review-features">
          <h4 id="max-review-features">Функции</h4>
          {features.length > 0 ? (
            <ul>
              {features.map(feature => (
                <li key={feature}>
                  <span aria-hidden="true">✓</span>
                  <span>{feature}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="max-project-review__placeholder">Без дополнительных функций</p>
          )}
        </section>

        <dl className="max-project-review__details">
          <div>
            <dt>Тип приложения</dt>
            <dd>{appTypeLabel}</dd>
          </div>
          <div>
            <dt>Аудитория</dt>
            <dd className={audience ? undefined : "max-project-review__placeholder"}>{audience || later}</dd>
          </div>
          <div>
            <dt>Стиль</dt>
            <dd>{styleLabel}</dd>
          </div>
          <div>
            <dt>Цвета бренда</dt>
            <dd className={brandColors ? undefined : "max-project-review__placeholder"}>
              {brandColors || "Подобрать автоматически"}
            </dd>
          </div>
        </dl>
      </div>
    </section>
  );
}
