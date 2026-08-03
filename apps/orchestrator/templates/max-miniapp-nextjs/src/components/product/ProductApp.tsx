"use client";

import dynamic from "next/dynamic";
import { useState } from "react";

import { useMaxApp } from "@/components/MaxAppProvider";
import { setMaxClosingConfirmation } from "@/lib/max/bridge";

const MaxButton = dynamic(
  () => import("@maxhub/max-ui").then((module) => module.Button),
  { ssr: false },
);

export default function ProductApp() {
  const { mode, user, error } = useMaxApp();
  const [saved, setSaved] = useState(false);

  function markReady() {
    setSaved(true);
    setMaxClosingConfirmation(false);
  }

  return (
    <main className="max-shell" data-testid="max-miniapp-preview">
      <section className="hero">
        <div className="platform-badge">
          <span className="platform-dot" />
          {mode === "max" ? "Открыто в MAX" : "Режим предпросмотра"}
        </div>
        <h1 className="hero-title">
          Готово к работе
        </h1>
        <p className="hero-copy">
          Безопасная MAX-сессия, нативная тема и webhook уже подключены к шаблону.
        </p>
      </section>

      <section className="profile-card">
        <div className="avatar">{user?.firstName?.slice(0, 1) || "M"}</div>
        <div>
          <h2 className="profile-title">
            {mode === "loading"
              ? "Подключаемся…"
              : error || `${user?.firstName || ""} ${user?.lastName || ""}`.trim()}
          </h2>
          <p className="muted">
            {mode === "max"
              ? "Личность подтверждена MAX"
              : mode === "error"
                ? "Проверьте подключение бота"
                : "В MAX здесь будет профиль реального пользователя"}
          </p>
        </div>
      </section>

      <section className="feature-grid">
        <article>
          <span>01</span>
          <h2>MAX Bridge</h2>
          <p>Тема, платформа, BackButton и lifecycle без лишней обвязки.</p>
        </article>
        <article>
          <span>02</span>
          <h2>Проверенная сессия</h2>
          <p>initData валидируется на сервере до доступа к данным пользователя.</p>
        </article>
        <article>
          <span>03</span>
          <h2>Webhook готов</h2>
          <p>Секретный заголовок и дедупликация защищают обработку событий.</p>
        </article>
      </section>

      <div className="bottom-action">
        <MaxButton
          size="large"
          stretched
          data-testid="max-primary-action"
          onClick={markReady}
        >
          {saved ? "Всё готово" : "Начать"}
        </MaxButton>
      </div>
    </main>
  );
}
