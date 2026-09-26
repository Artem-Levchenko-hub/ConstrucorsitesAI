"use client";

import { ArrowUp, Check, ChevronLeft, Clock, Minus, MoreHorizontal, Plus, Search, Users } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { LANDING_PROMPT_MAX_LENGTH, startWithPrompt } from "@/lib/landing-prompt";

import { Chips, PhoneFrame, Promo, Strip } from "./AppScreens";
import { ProductArt, type ArtKind } from "./ProductArt";

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

type Item = { id: string; title: string; note: string; price: number; art: ArtKind };

const menu: Item[] = [
  { id: "cap", title: "Капучино", note: "250 мл · на обычном молоке", price: 240, art: "cappuccino" },
  { id: "flat", title: "Флэт уайт", note: "Больше кофе, меньше молока", price: 260, art: "flatwhite" },
  { id: "cro", title: "Круассан", note: "С миндалём · из печи в 7:30", price: 190, art: "croissant" },
  { id: "che", title: "Чизкейк", note: "Кусок · нью-йорк", price: 290, art: "cheesecake" },
];
const goods: Item[] = [
  { id: "mon", title: "Монстера", note: "В кашпо 17 см · в наличии", price: 1400, art: "monstera" },
  { id: "fic", title: "Фикус", note: "Высота 60 см · в наличии", price: 980, art: "ficus" },
  { id: "zam", title: "Замиокулькас", note: "В кашпо 15 см · осталось 3", price: 1150, art: "zamioculcas" },
  { id: "cal", title: "Калатея", note: "Высота 40 см · в наличии", price: 890, art: "calathea" },
];
const slots = ["10:00", "12:30", "14:00", "16:30"];

/* Плашка и категории у каждого сценария свои: они отвечают на вопрос «зачем
   клиент открыл приложение именно сейчас», а он у кофейни и у магазина разный. */
const catalogueTop: Record<"cafe" | "shop", { promo: [string, string, string]; chips: string[] }> = {
  cafe: {
    promo: ["Заберите без очереди", "Будет готово к 8:40", "15 мин"],
    chips: ["Кофе", "Выпечка", "Десерты", "Чай"],
  },
  shop: {
    promo: ["Самовывоз сегодня", "Соберём за 2 часа", "бесплатно"],
    chips: ["Новое", "Неприхотливые", "Крупные", "Кашпо"],
  },
};

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
                <Promo
                  kicker={catalogueTop[scenario].promo[0]}
                  title={catalogueTop[scenario].promo[1]}
                  pill={catalogueTop[scenario].promo[2]}
                  tone={scenario}
                />
                <div className="ys-search">
                  <Search size={12} />
                  {scenario === "cafe" ? "Поиск по меню" : "Поиск по витрине"}
                </div>
                <Chips items={catalogueTop[scenario].chips} />
                {catalogue.map(item => (
                  <div className="ys-row ys-row--art" key={item.id}>
                    <ProductArt kind={item.art} />
                    <div className="ys-row-text">
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
                        <b className="ys-price">{item.price.toLocaleString("ru-RU")} ₽</b>
                      )}
                      <button type="button" onClick={() => add(item.id, 1)} aria-label={`Добавить ${item.title}`}><Plus size={14} /></button>
                    </div>
                  </div>
                ))}
                <Strip
                  title={scenario === "cafe" ? "Бонусная карта" : "Карта покупателя"}
                  note={scenario === "cafe" ? "Шестой кофе в подарок" : "5% возвращается бонусами"}
                  value={scenario === "cafe" ? "4 из 6" : "640 ₽"}
                />
              </>
            )}

            {scenario === "salon" && (
              <>
                <Promo kicker="Ближайшее окно" title={`Сегодня в ${slot}`} pill="60 мин" tone="salon" />
                <div className="ys-line"><span>Услуга</span><strong>Стрижка · 60 мин</strong></div>
                <div className="ys-line"><span>Мастер</span><strong>Анна <Check size={13} /></strong></div>
                <p className="ys-kicker">Сентябрь</p>
                <div className="ys-week">
                  {[["Пн", "22"], ["Вт", "23"], ["Ср", "24"], ["Чт", "25"], ["Пт", "26"]].map(([day, date], i) => (
                    <span key={day} className={i === 2 ? "is-on" : undefined}>
                      <em>{day}</em>
                      {date}
                    </span>
                  ))}
                </div>
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
                <p className="ys-note">
                  <Clock size={12} />
                  Подтверждение придёт в MAX
                </p>
                <p className="ys-kicker">Другие услуги</p>
                <div className="ys-line"><span>Окрашивание · 2 ч</span><strong>3 500 ₽</strong></div>
                <div className="ys-line"><span>Укладка · 40 мин</span><strong>1 200 ₽</strong></div>
                <div className="ys-line"><span>Уход для волос · 30 мин</span><strong>900 ₽</strong></div>
                <Strip title="Анна Соколова" note="Мастер · 6 лет в студии" value="4,9" />
              </>
            )}

            {scenario === "club" && (
              <>
                <div className="ys-hero-card ys-hero-card--club">
                  <Users size={26} />
                  <strong>Обсуждаем<br />«Маленького принца»</strong>
                  <span>20 сентября · 19:00 · онлайн</span>
                </div>
                <div className="ys-faces">
                  <span />
                  <span />
                  <span />
                  <small>идут 14 человек</small>
                </div>
                <p className="ys-kicker">Что будет на встрече</p>
                <ul className="ys-list">
                  <li>Обсудим героев и любимые цитаты</li>
                  <li>Выберем книгу на следующий месяц</li>
                  <li>Разыграем два бумажных издания</li>
                </ul>
                <p className="ys-kicker">Прошлые встречи</p>
                <div className="ys-line"><span>«Мартин Иден»</span><strong>6 сентября</strong></div>
                <div className="ys-line"><span>«Три товарища»</span><strong>23 августа</strong></div>
                <Strip title="Книга на октябрь" note="Голосование открыто до 28 сентября" value="5 книг" />
              </>
            )}
          </div>

          {/* Главное действие закреплено у нижнего края, как в настоящем
              приложении: клиент ищет его там и не должен докручивать экран. */}
          <div className="ys-bar">
            {(scenario === "cafe" || scenario === "shop") && (
              <>
                <span>{items ? `Корзина · ${items}` : "Корзина пуста"}</span>
                <b className={items ? "is-on" : undefined}>
                  {items ? `Оформить · ${total.toLocaleString("ru-RU")} ₽` : "Выберите позицию"}
                </b>
              </>
            )}
            {scenario === "salon" && <b className="is-on">Записаться на {slot}</b>}
            {scenario === "club" && (
              <button
                type="button"
                className={`ys-bar-button${joined ? " is-done" : " is-on"}`}
                onClick={() => { onTouch(); setJoined(v => !v); }}
              >
                {joined ? "Вы записаны" : "Участвовать"}
              </button>
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
