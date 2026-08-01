"use client";

import { useMaxApp } from "@/components/MaxAppProvider";
import { omniaMaxConfig as app } from "@/lib/omnia/max-config";

/**
 * Intentionally neutral first paint.
 *
 * This file is a generation canvas, not a reusable product template. The native
 * Google agent replaces it completely from the owner's brief. Keeping the
 * security/runtime substrate buildable lets the agent inspect and verify the
 * real MAX environment without anchoring its product design to generic cards.
 */
export default function MaxGenerationCanvas() {
  const { mode } = useMaxApp();

  return (
    <main className="generation-canvas" data-testid="max-generation-canvas">
      <section className="canvas-status" aria-live="polite">
        <span className="canvas-mark" aria-hidden="true" />
        <p>{mode === "loading" ? "Подключаем MAX…" : "Защищённое ядро готово"}</p>
      </section>
      <section className="canvas-copy">
        <p className="canvas-eyebrow">MAX MINI APP</p>
        <h1>{app.app_name}</h1>
        <p>
          Google AI-агент проектирует интерфейс и рабочие сценарии специально под
          ваше задание. Готового продуктового шаблона здесь нет.
        </p>
      </section>
      <div className="canvas-progress" aria-label="Подготовка приложения">
        <span />
      </div>
    </main>
  );
}
