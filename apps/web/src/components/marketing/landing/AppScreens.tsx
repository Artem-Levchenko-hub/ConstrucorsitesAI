/**
 * Экраны приложений для витрины.
 *
 * Это не скриншоты и не картинки, а живая разметка: те же элементы, что видит
 * клиент в настоящем приложении внутри MAX. За счёт этого они остаются чёткими
 * на любом экране, наследуют палитру и могут быть интерактивными там, где нужно
 * показать сценарий целиком, а не его картинку.
 */
import {
  CalendarDays,
  Check,
  ChevronLeft,
  Clock,
  MapPin,
  MoreHorizontal,
  Search,
  Users,
} from "lucide-react";

export type ScreenKind =
  | "cafe"
  | "cafe-after"
  | "salon"
  | "club"
  | "shop"
  | "status"
  | "loyalty";

export function ScreenChrome({
  name,
  section,
  children,
  compact = false,
}: {
  name: string;
  section: string;
  children: React.ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={`ys-screen${compact ? " ys-screen--compact" : ""}`}>
      <div className="ys-screen-top">
        <ChevronLeft size={15} />
        <div>
          <strong>{name}</strong>
          <span>{section}</span>
        </div>
        <MoreHorizontal size={16} />
      </div>
      <div className="ys-screen-body">{children}</div>
    </div>
  );
}

/** Каталог с ценами — самый частый первый сценарий. */
export function CafeScreen() {
  return (
    <ScreenChrome name="Смена" section="Кофейня">
      <p className="ys-kicker">Меню на сегодня</p>
      <div className="ys-hero-card ys-hero-card--cafe">
        <span>Сварим к вашему приходу</span>
      </div>
      {[
        ["Капучино", "250 мл · на обычном молоке", "240 ₽"],
        ["Флэт уайт", "Больше кофе, меньше молока", "260 ₽"],
        ["Раф ванильный", "300 мл", "320 ₽"],
      ].map(([title, note, price]) => (
        <div className="ys-row" key={title}>
          <div>
            <strong>{title}</strong>
            <small>{note}</small>
          </div>
          <b>{price}</b>
        </div>
      ))}
      <div className="ys-action">Заказать · 240 ₽</div>
    </ScreenChrome>
  );
}

/** Тот же экран после одной просьбы в чате: поиск по меню и заметная кнопка заказа. */
export function CafeAfterScreen() {
  return (
    <ScreenChrome name="Смена" section="Кофейня">
      <p className="ys-kicker">Меню на сегодня</p>
      <div className="ys-hero-card ys-hero-card--cafe">
        <span>Сварим к вашему приходу</span>
      </div>
      <div className="ys-search"><Search size={12} />Поиск по меню</div>
      {[
        ["Капучино", "250 мл · на обычном молоке", "240 ₽"],
        ["Флэт уайт", "Больше кофе, меньше молока", "260 ₽"],
        ["Раф ванильный", "300 мл", "320 ₽"],
      ].map(([title, note, price]) => (
        <div className="ys-row" key={title}>
          <div>
            <strong>{title}</strong>
            <small>{note}</small>
          </div>
          <b>{price}</b>
        </div>
      ))}
      <div className="ys-action is-on ys-action--big">Заказать · 240 ₽</div>
    </ScreenChrome>
  );
}

/** Запись на услугу: мастер, время, подтверждение. */
export function SalonScreen() {
  return (
    <ScreenChrome name="Форма" section="Студия красоты">
      <p className="ys-kicker">Запись</p>
      <div className="ys-line">
        <span>Услуга</span>
        <strong>Стрижка · 60 мин</strong>
      </div>
      <div className="ys-line">
        <span>Мастер</span>
        <strong>Анна <Check size={13} /></strong>
      </div>
      <p className="ys-kicker">Свободное время</p>
      <div className="ys-slots">
        <span>10:00</span>
        <span className="is-on">12:30</span>
        <span>14:00</span>
      </div>
      <p className="ys-note"><Clock size={12} />Подтверждение придёт в MAX</p>
      <div className="ys-action">Записаться на 12:30</div>
    </ScreenChrome>
  );
}

/** Сообщество: встречи и материалы для своих. */
export function ClubScreen() {
  return (
    <ScreenChrome name="Между строк" section="Книжный клуб">
      <div className="ys-hero-card ys-hero-card--club">
        <Users size={26} />
        <strong>Обсуждаем<br />«Маленького принца»</strong>
        <span>20 сентября · 19:00 · онлайн</span>
      </div>
      <p className="ys-kicker">Что будет на встрече</p>
      <ul className="ys-list">
        <li>Обсудим героев и любимые цитаты</li>
        <li>Выберем книгу на следующий месяц</li>
      </ul>
      <div className="ys-action">Участвовать</div>
    </ScreenChrome>
  );
}

/** Витрина товаров. */
export function ShopScreen() {
  return (
    <ScreenChrome name="Сад" section="Магазин растений">
      <p className="ys-kicker">Новое</p>
      <div className="ys-grid">
        {[
          ["Монстера", "1 400 ₽", "ys-tile--a"],
          ["Фикус", "980 ₽", "ys-tile--b"],
          ["Замиокулькас", "1 150 ₽", "ys-tile--c"],
          ["Калатея", "890 ₽", "ys-tile--d"],
        ].map(([title, price, tone]) => (
          <div className={`ys-tile ${tone}`} key={title}>
            <span />
            <strong>{title}</strong>
            <b>{price}</b>
          </div>
        ))}
      </div>
      <div className="ys-action">В корзину</div>
    </ScreenChrome>
  );
}

/** Статус заказа — то, ради чего клиент возвращается в приложение. */
export function StatusScreen() {
  return (
    <ScreenChrome name="Смена" section="Ваш заказ" compact>
      <p className="ys-kicker">Заказ №1482</p>
      <div className="ys-steps">
        {[
          ["Принят", true],
          ["Готовим", true],
          ["Можно забирать", false],
        ].map(([label, done]) => (
          <div className={`ys-step${done ? " is-done" : ""}`} key={String(label)}>
            <i />
            <span>{String(label)}</span>
          </div>
        ))}
      </div>
      <p className="ys-note"><MapPin size={12} />ул. Садовая, 12 · к 10:30</p>
    </ScreenChrome>
  );
}

/** Карта постоянного гостя. */
export function LoyaltyScreen() {
  return (
    <ScreenChrome name="Смена" section="Ваши бонусы" compact>
      <div className="ys-hero-card ys-hero-card--loyalty">
        <span>Накоплено</span>
        <strong>640</strong>
        <small>до бесплатного напитка — 360</small>
      </div>
      <div className="ys-line">
        <span>Визитов в этом месяце</span>
        <strong>7</strong>
      </div>
    </ScreenChrome>
  );
}

const registry: Record<ScreenKind, () => React.JSX.Element> = {
  cafe: CafeScreen,
  "cafe-after": CafeAfterScreen,
  salon: SalonScreen,
  club: ClubScreen,
  shop: ShopScreen,
  status: StatusScreen,
  loyalty: LoyaltyScreen,
};

export function AppScreen({ kind }: { kind: ScreenKind }) {
  const Screen = registry[kind];
  return <Screen />;
}

/** Лента экранов во всю ширину — визуальный «что отсюда выходит». */
export function ScreenCollage({
  screens,
  caption,
}: {
  screens: ScreenKind[];
  caption?: string;
}) {
  return (
    <figure className="ys-collage" aria-hidden="true">
      <div className="ys-collage-track">
        {screens.map((kind, i) => (
          <div className={`ys-collage-item ys-collage-item--${i % 5}`} key={`${kind}-${i}`}>
            <AppScreen kind={kind} />
          </div>
        ))}
      </div>
      {caption && <figcaption>{caption}</figcaption>}
    </figure>
  );
}
