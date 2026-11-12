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
import { ArrowRight, Search } from "lucide-react";

import { CAFE_DEMO, SHOP_DEMO } from "./demo-content";
import { EstimateScreen, FeedbackScreen, LessonScreen } from "./FeatureExamples";
import { OrderStatusScreen } from "./OrderStatusScreen";
import { ProductArt, type ArtKind } from "./ProductArt";
import { PhoneFrame, Promo, ScreenChrome, Strip } from "./ScreenChrome";

/** Строка категорий — первое, чем человек пользуется в каталоге. */
function Chips({ items }: { items: readonly string[] }) {
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

const CAFE_MENU = [
  ["Капучино", "250 мл", "240 ₽", "cappuccino"],
  ["Флэт уайт", "200 мл", "260 ₽", "flatwhite"],
  ["Круассан с миндалём", "1 штука", "190 ₽", "croissant"],
  ["Чизкейк", "1 порция", "290 ₽", "cheesecake"],
] as const;

/** Общее меню сохраняет одинаковые товары, название и время в обоих превью. */
function CafePreview({ enhanced = false }: { enhanced?: boolean }) {
  return (
    <ScreenChrome
      name={CAFE_DEMO.name}
      section="Кофейня"
      bar={
        enhanced ? (
          <div className="ys-checkout">
            <div><small>Корзина · 2</small><strong>К оплате 430 ₽</strong></div>
            <span>Оформить заказ<ArrowRight size={18} aria-hidden="true" /></span>
          </div>
        ) : (
          <><span>Корзина · 2</span><b>К оплате 430 ₽</b></>
        )
      }
    >
      <Promo kicker="Заберите без очереди" title={`Будет готово к ${CAFE_DEMO.readyAt}`} pill={CAFE_DEMO.wait} tone="cafe" />
      {enhanced && <div className="ys-search"><Search size={12} />Поиск по меню</div>}
      <Chips items={["Кофе", "Чай", "Выпечка", "Десерты"]} />
      <Tiles items={CAFE_MENU} />
      <p className="ys-kicker">Часто берут вместе</p>
      <PickRow art="croissant" title="Круассан с миндалём" note="Испекли в 7:30" price="190 ₽" />
      <PickRow art="cheesecake" title="Чизкейк нью-йорк" note="1 порция · 120 г" price="290 ₽" />
      <Strip title="Бонусная карта" note="Шестой кофе в подарок" value="4 из 6" />
    </ScreenChrome>
  );
}

/** Каталог с ценами — самый частый первый сценарий. */
export function CafeScreen() {
  return <CafePreview />;
}

/** Тот же экран после одной просьбы в чате: поиск по меню и заметная кнопка заказа. */
export function CafeAfterScreen() {
  return <CafePreview enhanced />;
}

/** Витрина товаров. */
export function ShopScreen() {
  return (
    <ScreenChrome
      name={SHOP_DEMO.name}
      section={SHOP_DEMO.section}
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

/** Карта постоянного гостя. */
export function LoyaltyScreen() {
  return (
    <ScreenChrome name={CAFE_DEMO.name} section="Ваши бонусы" bar={<b>Потратить бонусы</b>}>
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

const registry = {
  cafe: CafeScreen,
  "cafe-after": CafeAfterScreen,
  shop: ShopScreen,
  loyalty: LoyaltyScreen,
  estimate: EstimateScreen,
  lesson: LessonScreen,
  feedback: FeedbackScreen,
};
type ScreenKind = keyof typeof registry | "status";
const interactiveExamples: Partial<Record<ScreenKind, string>> = {
  status: "Статус и повтор заказа",
  estimate: "Калькулятор уборки",
  lesson: "Английский в поездке",
  feedback: "Отзыв о визите",
};

export function AppScreen({ kind, framed = true, allowRepeat = false }: { kind: ScreenKind; framed?: boolean; allowRepeat?: boolean }) {
  const Preview = kind === "status" ? null : registry[kind];
  const screen = Preview ? <Preview /> : <OrderStatusScreen allowRepeat={allowRepeat} />;
  return framed ? <PhoneFrame time={kind === "status" ? "10:28" : undefined}>{screen}</PhoneFrame> : screen;
}

export function ScreenCollage({ screens }: { screens: { kind: ScreenKind; description: string }[] }) {
  return (
    <figure className="ys-collage" tabIndex={0} aria-label="Примеры функций приложения">
      <div className="ys-collage-track">
        {screens.map(({ kind, description }) => {
          const label = interactiveExamples[kind];
          return (
            <div className="ys-collage-item" key={kind}>
              <p className="ys-collage-description">{description}</p>
              <div className={label ? "ys-collage-interactive" : undefined} role={label ? "group" : undefined}
                aria-label={label} aria-hidden={label ? undefined : true} inert={!label}>
                <AppScreen kind={kind} allowRepeat={kind === "status"} />
              </div>
            </div>
          );
        })}
      </div>
    </figure>
  );
}
