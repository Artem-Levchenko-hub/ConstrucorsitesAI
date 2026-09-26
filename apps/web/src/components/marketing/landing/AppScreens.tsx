/**
 * Экраны приложений для витрины.
 *
 * Это не скриншоты и не картинки, а живая разметка: те же элементы, что видит
 * клиент в настоящем приложении внутри MAX. За счёт этого они остаются чёткими
 * на любом экране, наследуют палитру и могут быть интерактивными там, где нужно
 * показать сценарий целиком, а не его картинку.
 *
 * Плотность здесь — не украшательство. Экран из подписи, трёх строк и кнопки
 * читается как набросок и обесценивает продукт: посетитель видит заготовку, а не
 * приложение, которое можно открыть завтра. Поэтому у каждого экрана есть то же,
 * что у настоящего: заметная плашка сверху, строка категорий, сетка карточек с
 * картинкой и ценой и закреплённая внизу полоса с главным действием.
 *
 * Товары нарисованы вектором (`ProductArt`), а не взяты картинками: витрина не
 * тянет ни байта со стороны, рисунок остаётся чётким на любом экране, и — главное
 * — предмет узнаётся. Градиентное пятно читалось как заглушка и обесценивало всё
 * вокруг себя, даже когда остальной экран был честным.
 */
import {
  Check,
  ChevronLeft,
  Clock,
  MapPin,
  MoreHorizontal,
  Search,
  Users,
} from "lucide-react";

import { ProductArt, type ArtKind } from "./ProductArt";

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
  bar,
  compact = false,
}: {
  name: string;
  section: string;
  children: React.ReactNode;
  /** Полоса главного действия, закреплённая у нижнего края экрана. */
  bar?: React.ReactNode;
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
      {bar && <div className="ys-bar">{bar}</div>}
    </div>
  );
}

/** Заметная плашка сверху: то, ради чего клиент открыл приложение сейчас. */
export function Promo({ kicker, title, pill, tone }: { kicker: string; title: string; pill?: string; tone: string }) {
  return (
    <div className={`ys-promo ys-promo--${tone}`}>
      <div>
        <span>{kicker}</span>
        <strong>{title}</strong>
      </div>
      {pill && <em>{pill}</em>}
    </div>
  );
}

/** Строка категорий — первое, чем человек пользуется в каталоге. */
export function Chips({ items }: { items: readonly string[] }) {
  return (
    <div className="ys-chips">
      {items.map((label, i) => (
        <span key={label} className={i === 0 ? "is-on" : undefined}>
          {label}
        </span>
      ))}
    </div>
  );
}

/** Сетка карточек с нарисованным товаром, ценой и кнопкой добавления. */
function Tiles({ items }: { items: readonly (readonly [string, string, string, ArtKind])[] }) {
  return (
    <div className="ys-grid">
      {items.map(([title, note, price, art]) => (
        <div className="ys-tile" key={title}>
          <ProductArt kind={art} />
          <strong>{title}</strong>
          <small>{note}</small>
          <div>
            <b>{price}</b>
            <i>+</i>
          </div>
        </div>
      ))}
    </div>
  );
}

/** Строка «часто берут»: тот же товар, но списком — так экран не превращается в одну сетку. */
function PickRow({ art, title, note, price }: { art: ArtKind; title: string; note: string; price: string }) {
  return (
    <div className="ys-row ys-row--art">
      <ProductArt kind={art} />
      <div className="ys-row-text">
        <strong>{title}</strong>
        <small>{note}</small>
      </div>
      <b>{price}</b>
    </div>
  );
}

/** Полоска-итог внизу раздела: бонусы, статус, голосование. */
export function Strip({ title, note, value }: { title: string; note: string; value: string }) {
  return (
    <div className="ys-strip">
      <div>
        <strong>{title}</strong>
        <small>{note}</small>
      </div>
      <b>{value}</b>
    </div>
  );
}

const CAFE_MENU = [
  ["Капучино", "250 мл", "240 ₽", "cappuccino"],
  ["Флэт уайт", "200 мл", "260 ₽", "flatwhite"],
  ["Круассан", "с миндалём", "190 ₽", "croissant"],
  ["Чизкейк", "кусок", "290 ₽", "cheesecake"],
] as const;

/** Каталог с ценами — самый частый первый сценарий. */
export function CafeScreen() {
  return (
    <ScreenChrome
      name="Смена"
      section="Кофейня"
      bar={
        <>
          <span>Корзина · 2</span>
          <b>Оплатить 410 ₽</b>
        </>
      }
    >
      <Promo kicker="Заберите без очереди" title="Будет готово к 8:40" pill="15 мин" tone="cafe" />
      <Chips items={["Кофе", "Выпечка", "Десерты", "Чай"]} />
      <Tiles items={CAFE_MENU} />
      <p className="ys-kicker">Часто берут вместе</p>
      <PickRow art="croissant" title="Круассан с миндалём" note="Из печи в 7:30" price="190 ₽" />
      <PickRow art="cheesecake" title="Чизкейк нью-йорк" note="Кусок 120 г" price="290 ₽" />
      <Strip title="Бонусная карта" note="Шестой кофе в подарок" value="4 из 6" />
    </ScreenChrome>
  );
}

/** Тот же экран после одной просьбы в чате: поиск по меню и заметная кнопка заказа. */
export function CafeAfterScreen() {
  return (
    <ScreenChrome
      name="Смена"
      section="Кофейня"
      bar={
        <>
          <span>Корзина · 2</span>
          <b className="is-on">Оплатить 410 ₽</b>
        </>
      }
    >
      <Promo kicker="Заберите без очереди" title="Будет готово к 8:40" pill="15 мин" tone="cafe" />
      <div className="ys-search">
        <Search size={12} />
        Поиск по меню
      </div>
      <Chips items={["Кофе", "Выпечка", "Десерты", "Чай"]} />
      <Tiles items={CAFE_MENU} />
      <p className="ys-kicker">Часто берут вместе</p>
      <PickRow art="croissant" title="Круассан с миндалём" note="Из печи в 7:30" price="190 ₽" />
      <PickRow art="cheesecake" title="Чизкейк нью-йорк" note="Кусок 120 г" price="290 ₽" />
      <Strip title="Бонусная карта" note="Шестой кофе в подарок" value="4 из 6" />
    </ScreenChrome>
  );
}

/** Запись на услугу: мастер, день, время, подтверждение. */
export function SalonScreen() {
  return (
    <ScreenChrome
      name="Форма"
      section="Студия красоты"
      bar={<b className="is-on">Записаться на 12:30</b>}
    >
      <Promo kicker="Ближайшее окно" title="Сегодня в 12:30" pill="60 мин" tone="salon" />
      <div className="ys-line">
        <span>Услуга</span>
        <strong>Стрижка · 60 мин</strong>
      </div>
      <div className="ys-line">
        <span>Мастер</span>
        <strong>
          Анна <Check size={13} />
        </strong>
      </div>
      <p className="ys-kicker">Сентябрь</p>
      <div className="ys-week">
        {[
          ["Пн", "22"],
          ["Вт", "23"],
          ["Ср", "24"],
          ["Чт", "25"],
          ["Пт", "26"],
        ].map(([day, date], i) => (
          <span key={day} className={i === 2 ? "is-on" : undefined}>
            <em>{day}</em>
            {date}
          </span>
        ))}
      </div>
      <div className="ys-slots">
        <span>10:00</span>
        <span className="is-on">12:30</span>
        <span>14:00</span>
        <span>17:30</span>
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
    </ScreenChrome>
  );
}

/** Сообщество: встречи и материалы для своих. */
export function ClubScreen() {
  return (
    <ScreenChrome name="Между строк" section="Книжный клуб" bar={<b className="is-on">Участвовать</b>}>
      <div className="ys-hero-card ys-hero-card--club">
        <Users size={26} />
        <strong>
          Обсуждаем
          <br />
          «Маленького принца»
        </strong>
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
      <div className="ys-line"><span>«Вино из одуванчиков»</span><strong>9 августа</strong></div>
      <Strip title="Книга на октябрь" note="Голосование открыто до 28 сентября" value="5 книг" />
    </ScreenChrome>
  );
}

/** Витрина товаров. */
export function ShopScreen() {
  return (
    <ScreenChrome
      name="Сад"
      section="Магазин растений"
      bar={
        <>
          <span>Корзина · 1</span>
          <b>Оформить · 1 400 ₽</b>
        </>
      }
    >
      <Promo kicker="Самовывоз сегодня" title="Соберём за 2 часа" pill="бесплатно" tone="shop" />
      <Chips items={["Новое", "Неприхотливые", "Крупные", "Кашпо"]} />
      <Tiles
        items={[
          ["Монстера", "в кашпо 17 см", "1 400 ₽", "monstera"],
          ["Фикус", "высота 60 см", "980 ₽", "ficus"],
          ["Замиокулькас", "в кашпо 15 см", "1 150 ₽", "zamioculcas"],
          ["Калатея", "высота 40 см", "890 ₽", "calathea"],
        ]}
      />
      <p className="ys-kicker">Часто покупают вместе</p>
      <PickRow art="ficus" title="Фикус Бенджамина" note="Высота 60 см · неприхотлив" price="980 ₽" />
      <PickRow art="zamioculcas" title="Замиокулькас" note="В кашпо 15 см · осталось 3" price="1 150 ₽" />
      <Strip title="Карта покупателя" note="5% возвращается бонусами" value="640 ₽" />
    </ScreenChrome>
  );
}

/** Статус заказа — то, ради чего клиент возвращается в приложение. */
export function StatusScreen() {
  return (
    <ScreenChrome name="Смена" section="Ваш заказ" bar={<b>Показать код · 1482</b>}>
      <Promo kicker="Заказ №1482" title="Можно забирать в 10:30" pill="2 мин" tone="status" />
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
      <p className="ys-kicker">В заказе</p>
      {[
        ["Капучино", "240 ₽"],
        ["Круассан", "190 ₽"],
      ].map(([title, price]) => (
        <div className="ys-row" key={title}>
          <div>
            <strong>{title}</strong>
          </div>
          <b>{price}</b>
        </div>
      ))}
      <p className="ys-note">
        <MapPin size={12} />
        ул. Садовая, 12 · к 10:30
      </p>
      <Strip title="Оплачено" note="Картой через ЮKassa · 26 сентября" value="430 ₽" />
      <p className="ys-kicker">Повторить прошлый заказ</p>
      <PickRow art="flatwhite" title="Флэт уайт" note="Брали 24 сентября" price="260 ₽" />
      <PickRow art="cheesecake" title="Чизкейк нью-йорк" note="Брали 21 сентября" price="290 ₽" />
    </ScreenChrome>
  );
}

/** Карта постоянного гостя. */
export function LoyaltyScreen() {
  return (
    <ScreenChrome name="Смена" section="Ваши бонусы" bar={<b>Потратить бонусы</b>}>
      <div className="ys-hero-card ys-hero-card--loyalty">
        <span>Накоплено</span>
        <strong>640</strong>
        <small>до бесплатного напитка — 360</small>
      </div>
      <p className="ys-kicker">Шестой кофе в подарок</p>
      <div className="ys-stamps">
        {[true, true, true, true, false, false].map((filled, i) => (
          <i key={i} className={filled ? "is-on" : undefined} />
        ))}
      </div>
      <div className="ys-line">
        <span>Визитов в этом месяце</span>
        <strong>7</strong>
      </div>
      <div className="ys-line">
        <span>Сгорит 30 сентября</span>
        <strong>120</strong>
      </div>
      <p className="ys-kicker">История</p>
      <div className="ys-line"><span>Начислено · 24 сентября</span><strong>+24</strong></div>
      <div className="ys-line"><span>Списано · 19 сентября</span><strong>−200</strong></div>
      <div className="ys-line"><span>Начислено · 17 сентября</span><strong>+41</strong></div>
      <Strip title="Приведите друга" note="Вам и ему — по 200 бонусов" value="200" />
    </ScreenChrome>
  );
}

/**
 * Рамка телефона вокруг экрана приложения.
 *
 * Мы продаём мини-приложение ВНУТРИ мессенджера, то есть вещь, которую человек
 * открывает в телефоне. Плоская карточка читается как сайт и заставляет
 * додумывать; телефон читается за полсекунды и без единого слова объяснений.
 *
 * Рамка намеренно скромная: тонкая грань, один мягкий свет снизу, никаких
 * бликов и отражений. Её работа — задать контекст, а не соревноваться с
 * содержимым за внимание.
 *
 * Строка состояния и полоска жеста нарисованы, а не взяты картинкой: так они
 * остаются чёткими на любом экране и не добавляют ни одного запроса к сети.
 * Для экранного диктора рамка невидима — она декорация вокруг уже описанного
 * содержимого.
 */
export function PhoneFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="ys-phone-frame">
      <div className="ys-phone-status" aria-hidden="true">
        <span className="ys-phone-time">9:41</span>
        <span className="ys-phone-island" />
        <span className="ys-phone-meters">
          <i className="ys-phone-signal" />
          <i className="ys-phone-battery" />
        </span>
      </div>
      <div className="ys-phone-viewport">{children}</div>
      <span className="ys-phone-home" aria-hidden="true" />
    </div>
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

export function AppScreen({ kind, framed = true }: { kind: ScreenKind; framed?: boolean }) {
  const Screen = registry[kind];
  // Рамка по умолчанию: все превью на витрине — это мини-приложения в телефоне.
  // Отключается там, где телефон уже нарисован снаружи.
  return framed ? (
    <PhoneFrame>
      <Screen />
    </PhoneFrame>
  ) : (
    <Screen />
  );
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
