"use client";

import { ArrowUp, Check, ChevronLeft, Minus, MoreHorizontal, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { LANDING_PROMPT_MAX_LENGTH, startWithPrompt } from "@/lib/landing-prompt";

import { PhoneFrame } from "./AppScreens";

/**
 * Первый экран витрины: запрос владельца печатается сам, а рядом появляется
 * приложение, которым можно пользоваться прямо на странице — добавить позицию,
 * выбрать время, записаться. Это не картинка приложения, а работающий сценарий:
 * посетитель видит не обещание, а результат, и может его потрогать.
 *
 * Состояние сценария живёт в отдельном компоненте с `key`: при смене вкладки он
 * пересоздаётся, поэтому корзина и печать сбрасываются сами, без ручного сброса
 * в эффекте.
 */

type Scenario = "cafe" | "salon" | "club" | "shop";

/* `event` — историческая метка сценария в статистике переходов. Ключи сценариев
   поменялись, а метки оставлены прежними, иначе прошлые данные воронки перестанут
   сходиться с новыми. */
const scenarios: Record<Scenario, { tab: string; event: string; prompt: string; brand: string; section: string }> = {
  cafe: { tab: "Кофейня", event: "coffee", brand: "Смена", section: "Кофейня",
    prompt: "Сделай приложение кофейни: меню, заказ к выдаче и бонусы для гостей." },
  salon: { tab: "Услуги", event: "services", brand: "Форма", section: "Студия красоты",
    prompt: "Хочу приложение студии: услуги, мастера и запись на свободное время." },
  club: { tab: "Клуб", event: "community", brand: "Между строк", section: "Книжный клуб",
    prompt: "Создай приложение клуба: ближайшие встречи и регистрация участников." },
  shop: { tab: "Магазин", event: "shop", brand: "Сад", section: "Магазин растений",
    prompt: "Нужен магазин растений: витрина с ценами и корзина с самовывозом." },
};

const order: Scenario[] = ["cafe", "salon", "club", "shop"];

const menu = [
  { id: "cap", title: "Капучино", note: "250 мл · на обычном молоке", price: 240 },
  { id: "flat", title: "Флэт уайт", note: "Больше кофе, меньше молока", price: 260 },
  { id: "raf", title: "Раф ванильный", note: "300 мл · сливочный", price: 320 },
];
const goods = [
  { id: "mon", title: "Монстера", note: "В наличии", price: 1400 },
  { id: "fic", title: "Фикус", note: "В наличии", price: 980 },
  { id: "zam", title: "Замиокулькас", note: "Осталось 3", price: 1150 },
];
const slots = ["10:00", "12:30", "14:00", "16:30"];

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

function ScenarioStage({ scenario, onTouch, tabs }: { scenario: Scenario; onTouch: () => void; tabs: React.ReactNode }) {
  const current = scenarios[scenario];
  const [cart, setCart] = useState<Record<string, number>>({});
  const [slot, setSlot] = useState("12:30");
  const [joined, setJoined] = useState(false);

  const add = (id: string, delta: number) => {
    onTouch();
    setCart(prev => {
      const count = Math.max(0, (prev[id] ?? 0) + delta);
      const next = { ...prev };
      if (count === 0) delete next[id];
      else next[id] = count;
      return next;
    });
  };

  const catalogue = scenario === "shop" ? goods : menu;
  const total = Object.entries(cart).reduce((sum, [id, n]) => {
    const item = catalogue.find(x => x.id === id);
    return sum + (item ? item.price * n : 0);
  }, 0);
  const items = Object.values(cart).reduce((a, b) => a + b, 0);

  return (
    <>
      {tabs}

      {/* Телефон показывает сторону КЛИЕНТА, а плашки рядом — сторону владельца:
          заказ пришёл и оплачен, приложение опубликовано. Два взгляда на один
          продукт сразу, без единого слова объяснений. */}
      <div className="ys-owner-note ys-owner-note--order" aria-hidden="true">
        <span className="ys-owner-dot" />
        <div>
          <strong>Новый заказ · 410 ₽</strong>
          <small>Оплачен через ЮKassa</small>
        </div>
      </div>
      <div className="ys-owner-note ys-owner-note--live" aria-hidden="true">
        <span className="ys-owner-dot ys-owner-dot--live" />
        <div>
          <strong>Приложение опубликовано</strong>
          <small>В чат-боте вашей организации</small>
        </div>
      </div>

      <div className="ys-phone" aria-live="polite">
        <PhoneFrame>
        <div className="ys-screen">
          <div className="ys-screen-top">
            <ChevronLeft size={15} />
            <div><strong>{current.brand}</strong><span>{current.section}</span></div>
            <MoreHorizontal size={16} />
          </div>
          <div className="ys-screen-body">
            {(scenario === "cafe" || scenario === "shop") && (
              <>
                <p className="ys-kicker">{scenario === "cafe" ? "Меню на сегодня" : "Витрина"}</p>
                {catalogue.map(item => (
                  <div className="ys-row" key={item.id}>
                    <div>
                      <strong>{item.title}</strong>
                      <small>{item.note}</small>
                    </div>
                    <div className="ys-stepper">
                      {cart[item.id] ? (
                        <>
                          <button type="button" onClick={() => add(item.id, -1)} aria-label={`Убрать ${item.title}`}><Minus size={14} /></button>
                          <b>{cart[item.id]}</b>
                        </>
                      ) : (
                        <b className="ys-price">{item.price} ₽</b>
                      )}
                      <button type="button" onClick={() => add(item.id, 1)} aria-label={`Добавить ${item.title}`}><Plus size={14} /></button>
                    </div>
                  </div>
                ))}
                <div className={`ys-action${items ? " is-on" : ""}`}>
                  {items ? `Оформить · ${total.toLocaleString("ru-RU")} ₽` : "Выберите позицию"}
                </div>
              </>
            )}

            {scenario === "salon" && (
              <>
                <div className="ys-line"><span>Услуга</span><strong>Стрижка · 60 мин</strong></div>
                <div className="ys-line"><span>Мастер</span><strong>Анна <Check size={13} /></strong></div>
                <p className="ys-kicker">Свободное время</p>
                <div className="ys-slots">
                  {slots.map(time => (
                    <button
                      key={time}
                      type="button"
                      className={slot === time ? "is-on" : undefined}
                      aria-pressed={slot === time}
                      onClick={() => { onTouch(); setSlot(time); }}
                    >
                      {time}
                    </button>
                  ))}
                </div>
                <p className="ys-note">Подтверждение придёт в MAX</p>
                <div className="ys-action is-on">Записаться на {slot}</div>
              </>
            )}

            {scenario === "club" && (
              <>
                <div className="ys-hero-card ys-hero-card--club">
                  <strong>Обсуждаем<br />«Маленького принца»</strong>
                  <span>20 сентября · 19:00 · онлайн</span>
                </div>
                <p className="ys-kicker">Что будет на встрече</p>
                <ul className="ys-list">
                  <li>Обсудим героев и любимые цитаты</li>
                  <li>Выберем книгу на следующий месяц</li>
                </ul>
                <button
                  type="button"
                  className={`ys-action${joined ? " is-done" : " is-on"}`}
                  onClick={() => { onTouch(); setJoined(v => !v); }}
                >
                  {joined ? "Вы записаны" : "Участвовать"}
                </button>
              </>
            )}
          </div>
        </div>
        </PhoneFrame>
      </div>
    </>
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
 * Форма живёт ВЫШЕ демонстрации сценария: та пересоздаётся при переключении
 * вкладок, и написанное внутри неё стиралось бы на каждом переключении.
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

  // Пустое поле — это не ошибка: человек просто хочет зарегистрироваться.
  // Переход обычный, а не через маршрутизатор: витрина и кабинет — разные части
  // приложения, а хук маршрутизатора требует смонтированного роутера и ломает
  // статический рендер витрины в тестах и в предпросмотре.
  const submit = () => startWithPrompt(value, (href) => { window.location.assign(href); });

  const length = Array.from(value.trim()).length;
  const tooLong = length > LANDING_PROMPT_MAX_LENGTH;

  return (
    <form
      className="ys-stage-prompt"
      onSubmit={(event) => { event.preventDefault(); if (!tooLong) submit(); }}
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
            if (!tooLong) submit();
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
        aria-label={value.trim() ? "Создать приложение по этому описанию" : "Начать бесплатно"}
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

export function HeroStage() {
  const [scenario, setScenario] = useState<Scenario>("cafe");
  const reduced = useReducedMotion();
  const touched = useRef(false);
  const onTouch = useCallback(() => { touched.current = true; }, []);

  // Автоперебор сценариев, пока посетитель ни на что не нажал.
  useEffect(() => {
    if (reduced) return;
    const id = window.setInterval(() => {
      if (touched.current || document.hidden) return;
      setScenario(prev => order[(order.indexOf(prev) + 1) % order.length]);
    }, 9000);
    return () => window.clearInterval(id);
  }, [reduced]);

  return (
    <div className="ys-stage" data-testid="landing-example">
      <PromptComposer demo={scenarios[scenario].prompt} />
      <ScenarioStage
        key={scenario}
        scenario={scenario}
        onTouch={onTouch}
        tabs={
          <div className="ys-tabs" role="group" aria-label="Пример приложения">
            {order.map((key, i) => (
              <div key={key}>
                {i > 0 && <span className="ys-tabs-dot" aria-hidden="true" />}
                <button
                  type="button"
                  aria-pressed={scenario === key}
                  data-marketing="scenario_select"
                  data-placement={scenarios[key].event}
                  onClick={() => { onTouch(); setScenario(key); }}
                >
                  {scenarios[key].tab}
                </button>
              </div>
            ))}
          </div>
        }
      />

      <p className="ys-stage-caption">
        Пример интерфейса — работающий, его можно нажать. Содержание и функции вы задаёте для своего бизнеса.
      </p>
    </div>
  );
}
