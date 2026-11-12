"use client";

import { ArrowUp, ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { LANDING_PROMPT_MAX_LENGTH, startWithPrompt } from "@/lib/landing-prompt";

import { CatalogDemo } from "./CatalogDemo";
import { ClubDemo } from "./ClubDemo";
import type { DemoScenario as Scenario } from "./demo-content";
import { demoClock } from "./demo-date";
import { SalonDemo } from "./SalonDemo";
import { PhoneFrame } from "./ScreenChrome";

// Keep the original analytics values for existing reports.
const scenarios: Record<Scenario, { tab: string; event: string; prompt: string }> = {
  cafe: { tab: "Кофейня", event: "coffee", prompt: "Сделай приложение кофейни: меню, заказ к выдаче и бонусы для гостей." },
  salon: { tab: "Услуги", event: "services", prompt: "Хочу приложение студии красоты: услуги, мастера и запись на свободное время." },
  club: { tab: "Клуб", event: "community", prompt: "Создай приложение книжного клуба: ближайшие встречи и регистрация участников." },
  shop: { tab: "Магазин", event: "shop", prompt: "Нужен магазин растений: витрина с ценами и корзина с самовывозом." },
};

const order: Scenario[] = ["cafe", "salon", "club", "shop"];

const noMotion = { subscribe: () => () => {}, get: () => false };

/** Медиазапрос через внешнее хранилище: без setState внутри эффекта и без падения там, где matchMedia нет. */
function useReducedMotion() {
  const store = useMemo(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return noMotion;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    return {
      subscribe: (onChange: () => void) => {
        query.addEventListener("change", onChange);
        return () => query.removeEventListener("change", onChange);
      },
      get: () => query.matches,
    };
  }, []);
  return useSyncExternalStore(store.subscribe, store.get, () => false);
}

function ScenarioScreen({ scenario, now, onClockRefresh }: { scenario: Scenario; now: number; onClockRefresh: (now: number) => void }) {
  return (
    <PhoneFrame time={scenario === "salon" ? demoClock(now) : undefined}>
      {scenario === "salon" ? <SalonDemo now={now} onClockRefresh={onClockRefresh} />
        : scenario === "club" ? <ClubDemo now={now} />
        : <CatalogDemo kind={scenario} />}
    </PhoneFrame>
  );
}

/**
 * Поле первого экрана — настоящее, а не витрина.
 *
 * Пока посетитель ничего не написал, в подсказке сама печатается задача текущего
 * сценария: страница остаётся живой и показывает, какого рода запрос тут ждут.
 * Как только он начинает печатать, подсказка замолкает и больше не возвращается —
 * подменять или дописывать чужой текст недопустимо.
 *
 * Форма одна для всех сценариев и сохраняет собственный текст при перелистывании.
 *
 * По отправке текст сохраняется у посетителя и происходит переход на регистрацию.
 * Подхватывает его кабинет: см. `takeLandingPrompt`.
 */
function PromptComposer({ demo }: { demo: string }) {
  const reduced = useReducedMotion();
  const [value, setValue] = useState("");
  const [shown, setShown] = useState(0);
  const own = value.length > 0;

  useEffect(() => {
    if (own || reduced) return;
    let i = 0;
    const id = window.setInterval(() => {
      i += 1;
      // Состояние меняется только из обработчика таймера, а не синхронно внутри
      // эффекта: синхронный вызов запускает каскад перерисовок.
      setShown(i);
      if (i >= demo.length) window.clearInterval(id);
    }, 22);
    return () => window.clearInterval(id);
  }, [demo, own, reduced]);

  // При смене вкладки счётчик остаётся от прошлой задачи, поэтому длину режем по
  // текущей: иначе на такте между сменой и первым тиком видна обрезка чужого текста.
  const hint = reduced ? demo : demo.slice(0, Math.min(shown, demo.length));

  // Переход обычный, а не через маршрутизатор: витрина и кабинет — разные части
  // приложения, а хук маршрутизатора требует смонтированного роутера и ломает
  // статический рендер витрины в тестах и в предпросмотре.
  const submit = () => startWithPrompt(value, (href) => { window.location.assign(href); });

  const length = Array.from(value.trim()).length;
  const tooLong = length > LANDING_PROMPT_MAX_LENGTH;
  const canSubmit = length > 0 && !tooLong;

  return (
    <form
      className="ys-stage-prompt"
      onSubmit={(event) => { event.preventDefault(); if (canSubmit) submit(); }}
    >
      <label className="ys-stage-label" htmlFor="yl-hero-prompt">Вы пишете</label>
      <textarea
        id="yl-hero-prompt"
        className="ys-stage-input"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          // Enter отправляет, Shift+Enter переносит строку — как в любом чате.
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            if (canSubmit) submit();
          }
        }}
        placeholder={hint}
        rows={2}
        spellCheck={false}
        aria-describedby={tooLong ? "yl-hero-prompt-limit" : undefined}
        aria-invalid={tooLong}
      />
      {!own && !reduced && <i className="ys-caret" aria-hidden="true" />}
      <button
        type="submit"
        className="ys-stage-send"
        data-marketing="signup_click"
        data-placement="hero_prompt"
        aria-label="Создать приложение по этому описанию"
        disabled={!canSubmit}
      >
        <ArrowUp size={16} />
      </button>
      {tooLong && (
        <p id="yl-hero-prompt-limit" className="ys-stage-limit">
          {length} из {LANDING_PROMPT_MAX_LENGTH} символов — сократите описание.
        </p>
      )}
    </form>
  );
}

export function HeroStage({ initialNow }: { initialNow: number }) {
  const [scenario, setScenario] = useState<Scenario>("cafe");
  const [now, setNow] = useState(initialNow);
  const carousel = useRef<HTMLElement>(null);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const carouselId = useId();
  const activeIndex = order.indexOf(scenario);
  const previous = order[(activeIndex + order.length - 1) % order.length];
  const next = order[(activeIndex + 1) % order.length];
  const slideId = (key: Scenario) => `${carouselId}-${key}`;

  function showScenario(key: Scenario) {
    setScenario(key);
    const stage = carousel.current;
    if (!stage || window.innerWidth < 720) return;
    const bounds = stage.getBoundingClientRect();
    const headerBottom = document.querySelector(".yl-header")?.getBoundingClientRect().bottom ?? 0;
    if (bounds.top < headerBottom || bounds.bottom > window.innerHeight) {
      stage.scrollIntoView?.({ block: "nearest", behavior: reduceMotion ? "instant" : "smooth" });
    }
  }

  return (
    <div className="ys-stage" data-testid="landing-example">
      <PromptComposer demo={scenarios[scenario].prompt} />
      <div className="ys-tabs" role="group" aria-label="Пример приложения">
        {order.map((key, i) => (
          <div key={key}>
            {i > 0 && <span className="ys-tabs-dot" aria-hidden="true" />}
            <button
              type="button"
              aria-pressed={scenario === key}
              aria-controls={slideId(key)}
              data-marketing="scenario_select"
              data-placement={scenarios[key].event}
              onClick={() => showScenario(key)}
            >
              {scenarios[key].tab}
            </button>
          </div>
        ))}
      </div>
      <section ref={carousel} className="ys-coverflow" aria-label="Примеры приложений" aria-roledescription="карусель">
        <div className="ys-coverflow-track">
          {order.map((key, index) => {
            const distance = (index - activeIndex + order.length) % order.length;
            const position = distance === 0 ? "active" : distance === 1 ? "next" : distance === order.length - 1 ? "previous" : "hidden";
            return <div key={key} id={slideId(key)} className="ys-coverflow-slide" data-scenario={key} data-position={position}
              role="group" aria-roledescription="слайд" aria-label={`${index + 1} из ${order.length}. ${scenarios[key].tab}`}
              aria-hidden={position === "hidden" ? true : undefined} inert={position === "hidden"}>
              <div data-phone-content aria-hidden={position === "active" ? undefined : true} inert={position !== "active"}>
                <ScenarioScreen scenario={key} now={now} onClockRefresh={setNow} />
              </div>
            </div>;
          })}
          <button type="button" className="ys-coverflow-select ys-coverflow-select--previous" aria-label={`Показать: ${scenarios[previous].tab}`}
            aria-controls={slideId(previous)} data-marketing="scenario_select" data-placement={scenarios[previous].event} onClick={() => showScenario(previous)} />
          <button type="button" className="ys-coverflow-select ys-coverflow-select--next" aria-label={`Показать: ${scenarios[next].tab}`}
            aria-controls={slideId(next)} data-marketing="scenario_select" data-placement={scenarios[next].event} onClick={() => showScenario(next)} />
        </div>
        <div className="ys-coverflow-navigation">
          <button type="button" aria-label="Предыдущий экран" aria-controls={slideId(previous)} data-marketing="scenario_select" data-placement={scenarios[previous].event} onClick={() => showScenario(previous)}><ChevronLeft size={18} aria-hidden="true" /></button>
          <span aria-hidden="true">0{activeIndex + 1}<i> / </i>04</span>
          <button type="button" aria-label="Следующий экран" aria-controls={slideId(next)} data-marketing="scenario_select" data-placement={scenarios[next].event} onClick={() => showScenario(next)}><ChevronRight size={18} aria-hidden="true" /></button>
        </div>
        <p className="sr-only" aria-live="polite" aria-atomic="true">{activeIndex + 1} из {order.length}: {scenarios[scenario].tab}</p>
      </section>

      <p className="ys-stage-caption">
        Пример интерфейса с демонстрационными данными. Попробуйте оформить заказ, записаться на услугу или проголосовать за книгу.
      </p>
    </div>
  );
}
