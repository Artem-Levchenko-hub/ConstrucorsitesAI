"use client";

import dynamic from "next/dynamic";
import type { CSSProperties } from "react";
import { useMemo, useState } from "react";

import { useMaxApp } from "@/components/MaxAppProvider";
import { omniaMaxConfig as app } from "@/lib/omnia/max-config";
import { setMaxClosingConfirmation } from "@/lib/max/bridge";

const MaxButton = dynamic(
  () => import("@maxhub/max-ui").then((module) => module.Button),
  { ssr: false },
);

const TYPE_LABELS = {
  loyalty: "Программа лояльности",
  catalog: "Каталог и заказы",
  booking: "Онлайн-запись",
  event: "Событие",
  education: "Обучение",
  custom: "Сервис",
} as const;

const TYPE_HINTS = {
  loyalty: ["Баланс и уровень", "Награды", "История действий"],
  catalog: ["Актуальный каталог", "Быстрый выбор", "Статус заказа"],
  booking: ["Свободные слоты", "Подтверждение", "Напоминания"],
  event: ["Программа", "Билет участника", "Важные уведомления"],
  education: ["Учебный план", "Прогресс", "Материалы"],
  custom: ["Личный профиль", "Основной сценарий", "Поддержка"],
} as const;

function firstHex(value: string): string | null {
  return value.match(/#[0-9a-f]{6}/i)?.[0] ?? null;
}

export default function Home() {
  const { mode, user, error } = useMaxApp();
  const [completed, setCompleted] = useState(false);
  const accent = firstHex(app.brand_colors) || (app.style === "bright" ? "#ff5c35" : "#5f4ae6");
  const features = useMemo(
    () => (app.features.length ? [...app.features] : [...TYPE_HINTS[app.app_type]]).slice(0, 6),
    [],
  );
  const content = useMemo(() => app.content.filter((item) => item.active).slice(0, 6), []);
  const shellStyle = { "--app-accent": accent } as CSSProperties;

  function handlePrimaryAction() {
    setCompleted(true);
    setMaxClosingConfirmation(false);
  }

  return (
    <main
      className="max-shell"
      data-style={app.style}
      data-testid="max-miniapp-preview"
      style={shellStyle}
    >
      <section className="hero">
        <div className="hero-topline">
          <span className="platform-badge">
            <span className="platform-dot" />
            {mode === "max" ? "Открыто в MAX" : "Безопасное превью"}
          </span>
          <span className="age-badge">{app.legal.age_rating}</span>
        </div>
        <p className="eyebrow">{TYPE_LABELS[app.app_type]}</p>
        <h1 className="hero-title">{app.app_name}</h1>
        <p className="hero-copy">{app.summary}</p>
        {app.audience && <p className="audience">Для кого: {app.audience}</p>}
      </section>

      <section className="profile-card" aria-label="Профиль MAX">
        <div className="avatar">{user?.firstName?.slice(0, 1) || app.app_name.slice(0, 1)}</div>
        <div className="profile-copy">
          <p className="profile-kicker">Личный кабинет</p>
          <h2 className="profile-title">
            {mode === "loading"
              ? "Подключаемся…"
              : error || `${user?.firstName || "Гость"} ${user?.lastName || ""}`.trim()}
          </h2>
          <p className="muted">
            {mode === "max" ? "Личность подтверждена MAX" : "В MAX здесь будет реальный профиль"}
          </p>
        </div>
        <span className="profile-status">●</span>
      </section>

      {content.length > 0 && (
        <section className="content-section" aria-labelledby="content-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Доступно сейчас</p>
              <h2 id="content-title">Выберите вариант</h2>
            </div>
            <span>{content.length}</span>
          </div>
          <div className="content-list">
            {content.map((item) => (
              <article key={item.id} className="content-card">
                <div className="content-index">{item.title.slice(0, 1)}</div>
                <div className="content-copy">
                  <h3>{item.title}</h3>
                  {item.description && <p>{item.description}</p>}
                </div>
                <div className="content-action">
                  {item.price && <strong>{item.price}</strong>}
                  <span>{item.action_label} →</span>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      <section className="features-section" aria-labelledby="features-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Возможности</p>
            <h2 id="features-title">Всё важное под рукой</h2>
          </div>
        </div>
        <div className="feature-grid">
          {features.map((feature, index) => (
            <article key={feature}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <h3>{feature}</h3>
              <p>Доступно в одном безопасном сценарии внутри MAX.</p>
            </article>
          ))}
        </div>
      </section>

      <nav className="legal-links" aria-label="Документы и поддержка">
        <a href="/support">Поддержка</a>
        <a href="/legal/privacy">Конфиденциальность</a>
        <a href="/legal/terms">Условия</a>
      </nav>

      <div className="bottom-action">
        <div className="bottom-copy">
          <span>{completed ? "Готово" : "Следующий шаг"}</span>
          <strong>{completed ? "Действие сохранено" : app.primary_action || "Открыть сервис"}</strong>
        </div>
        <div className="button-wrap">
          <MaxButton size="large" stretched data-testid="max-primary-action" onClick={handlePrimaryAction}>
            {completed ? "Всё готово" : app.primary_action || "Начать"}
          </MaxButton>
        </div>
      </div>
    </main>
  );
}
