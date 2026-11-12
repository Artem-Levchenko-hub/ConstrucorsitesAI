import { Check, ChevronRight, CircleAlert, Coffee, Minus, Plus, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { CAFE_DEMO, SHOP_DEMO } from "./demo-content";
import { ProductArt, type ArtKind } from "./ProductArt";
import { Promo, ScreenChrome, Strip } from "./ScreenChrome";
import "./catalog-demo.css";

type CatalogKind = "cafe" | "shop";
type Choice = { id: string; label: string; extra?: number; summary?: string };
type OptionGroup = { id: string; label: string; choices: readonly [Choice, ...Choice[]] };
type Selections = Record<string, string>;
type Product = {
  id: string;
  title: string;
  note?: string;
  price: number;
  art: ArtKind;
  ingredients?: readonly string[];
  options?: readonly OptionGroup[];
  heightCm?: number;
};
type Item = Product & { categories: string[] };
type CartVariant = { key: string; item: Item; unitPrice: number; summary: string };
type CartLine = CartVariant & { count: number };
type CatalogReturn = { focus: string; scrollTop: number };
type View =
  | { name: "catalog" | "order" }
  | { name: "cart" | "loyalty"; back: CatalogReturn }
  | { name: "ingredients"; item: Item; back: CatalogReturn }
  | { name: "customize"; item: Item; back: CatalogReturn; selections: Selections; count: number };

const milk: OptionGroup = {
  id: "milk", label: "Молоко", choices: [
    { id: "cow", label: "Коровье" },
    { id: "oat", label: "Овсяное", extra: 40, summary: "Овсяное молоко" },
    { id: "coconut", label: "Кокосовое", extra: 50, summary: "Кокосовое молоко" },
  ],
};
const extraMilk: OptionGroup = {
  ...milk, choices: [
    { id: "none", label: "Без молока" },
    { id: "cow", label: "Коровье · 30 мл", extra: 20, summary: "Коровье молоко · 30 мл" },
    { id: "oat", label: "Овсяное · 30 мл", extra: 40, summary: "Овсяное молоко · 30 мл" },
  ],
};
const syrup: OptionGroup = {
  id: "syrup", label: "Сироп", choices: [
    { id: "none", label: "Без сиропа" },
    { id: "vanilla", label: "Ванильный", extra: 30, summary: "Ванильный сироп" },
    { id: "caramel", label: "Карамельный", extra: 30, summary: "Карамельный сироп" },
  ],
};
const marshmallow: OptionGroup = {
  id: "marshmallow", label: "Маршмеллоу", choices: [
    { id: "none", label: "Без маршмеллоу" },
    { id: "yes", label: "Добавить", extra: 30, summary: "Маршмеллоу" },
  ],
};
const lemon: OptionGroup = {
  id: "lemon", label: "Лимон", choices: [
    { id: "none", label: "Без лимона" },
    { id: "yes", label: "Добавить", extra: 15, summary: "Лимон" },
  ],
};
const honey: OptionGroup = {
  id: "honey", label: "Мёд", choices: [
    { id: "none", label: "Без мёда" },
    { id: "yes", label: "Добавить", extra: 25, summary: "Мёд" },
  ],
};
const milkCoffeeOptions = [milk, syrup, marshmallow];
const teaOptions = [lemon, honey];

const cafeMenu = {
  "Кофе": [
    {
      id: "cap", title: "Капучино", note: "250 мл · молочная пена", price: 240, art: "cappuccino",
      ingredients: ["Эспрессо — 1 шот (30 мл)", "Молоко — 170 мл", "Молочная пена — 50 мл"], options: milkCoffeeOptions,
    },
    {
      id: "flat", title: "Флэт уайт", note: "200 мл · двойной эспрессо", price: 260, art: "flatwhite",
      ingredients: ["Эспрессо — 2 шота (60 мл)", "Молоко — 140 мл"], options: milkCoffeeOptions,
    },
    {
      id: "latte", title: "Латте", note: "300 мл · мягкий вкус", price: 270, art: "cappuccino",
      ingredients: ["Эспрессо — 1 шот (30 мл)", "Молоко — 270 мл"], options: milkCoffeeOptions,
    },
    {
      id: "americano", title: "Американо", note: "250 мл · чёрный кофе", price: 180, art: "flatwhite",
      ingredients: ["Эспрессо — 2 шота (60 мл)", "Горячая вода — 190 мл"], options: [extraMilk, syrup],
    },
    {
      id: "espresso", title: "Двойной эспрессо", note: "60 мл · насыщенный вкус", price: 160, art: "flatwhite",
      ingredients: ["Эспрессо — 2 шота (60 мл)"], options: [syrup],
    },
  ],
  "Чай": [
    {
      id: "earl", title: "Эрл Грей", note: "350 мл · с бергамотом", price: 170, art: "tea",
      ingredients: ["Чёрный чай", "Бергамот", "Вода"], options: teaOptions,
    },
    {
      id: "jasmine", title: "Жасминовый чай", note: "350 мл · зелёный чай", price: 190, art: "tea",
      ingredients: ["Зелёный чай", "Цветы жасмина", "Вода"], options: teaOptions,
    },
    {
      id: "mint", title: "Мятный чай", note: "350 мл · травяной", price: 180, art: "tea",
      ingredients: ["Листья мяты", "Мелисса", "Вода"], options: teaOptions,
    },
    {
      id: "buckthorn", title: "Облепиховый чай", note: "350 мл · с апельсином", price: 240, art: "tea",
      ingredients: ["Облепиха", "Апельсин", "Сахар", "Вода"], options: teaOptions,
    },
    {
      id: "ginger", title: "Имбирный чай", note: "350 мл · с лимоном", price: 220, art: "tea",
      ingredients: ["Имбирь", "Лимон", "Вода"], options: [honey],
    },
  ],
  "Выпечка": [
    {
      id: "cro-butter", title: "Классический круассан", note: "Испекли в 7:30", price: 160, art: "croissant",
      ingredients: ["Пшеничная мука", "Сливочное масло", "Молоко", "Сахар", "Дрожжи", "Соль"],
    },
    {
      id: "cro", title: "Круассан с миндалём", note: "С миндальным кремом", price: 190, art: "croissant",
      ingredients: ["Слоёное тесто на сливочном масле", "Миндаль", "Яйцо", "Сахар"],
    },
    {
      id: "cro-chocolate", title: "Круассан с шоколадом", note: "С тёмным шоколадом", price: 210, art: "croissant",
      ingredients: ["Слоёное тесто на сливочном масле", "Тёмный шоколад"],
    },
    {
      id: "cro-cheese", title: "Круассан с сыром", note: "С чеддером", price: 230, art: "croissant",
      ingredients: ["Слоёное тесто на сливочном масле", "Чеддер"],
    },
    {
      id: "cro-cherry", title: "Круассан с вишней", note: "С вишнёвой начинкой", price: 220, art: "croissant",
      ingredients: ["Слоёное тесто на сливочном масле", "Вишня", "Сахар", "Кукурузный крахмал"],
    },
  ],
  "Десерты": [
    {
      id: "che", title: "Чизкейк", note: "1 порция · Нью-Йорк", price: 290, art: "cheesecake",
      ingredients: ["Сливочный сыр", "Песочное печенье", "Яйцо", "Сливки", "Сахар"],
    },
    {
      id: "che-berry", title: "Малиновый чизкейк", note: "1 порция · с ягодным соусом", price: 320, art: "cheesecake",
      ingredients: ["Сливочный сыр", "Песочное печенье", "Яйцо", "Сливки", "Малина", "Сахар"],
    },
    {
      id: "medovik", title: "Медовик", note: "1 порция · со сметанным кремом", price: 280, art: "cheesecake",
      ingredients: ["Пшеничная мука", "Мёд", "Яйцо", "Сливочное масло", "Сметана", "Сахар"],
    },
    {
      id: "carrot", title: "Морковный торт", note: "1 порция · с грецкими орехами", price: 310, art: "cheesecake",
      ingredients: ["Морковь", "Пшеничная мука", "Яйцо", "Грецкий орех", "Сливочный сыр", "Корица", "Сахар"],
    },
    {
      id: "napoleon", title: "Торт «Наполеон»", note: "1 порция · с заварным кремом", price: 300, art: "cheesecake",
      ingredients: ["Слоёное тесто на сливочном масле", "Молоко", "Яйцо", "Сахар", "Ваниль"],
    },
  ],
} satisfies Record<string, Product[]>;

const catalogs: Record<CatalogKind, { categories: string[]; items: Item[] }> = {
  cafe: {
    categories: Object.keys(cafeMenu),
    items: Object.entries(cafeMenu).flatMap(([category, products]) => products.map(item => ({ ...item, categories: [category] }))),
  },
  shop: {
    categories: ["Новое", "Неприхотливые", "Крупные", "Кашпо"],
    items: [
      { id: "mon", title: "Монстера", heightCm: 75, price: 1400, art: "monstera", categories: ["Новое", "Крупные"] },
      { id: "fic", title: "Фикус", heightCm: 60, price: 980, art: "ficus", categories: ["Новое", "Неприхотливые", "Крупные"] },
      { id: "zam", title: "Замиокулькас", heightCm: 50, price: 1150, art: "zamioculcas", categories: ["Новое", "Неприхотливые"] },
      { id: "cal", title: "Калатея", heightCm: 40, price: 890, art: "calathea", categories: ["Новое"] },
      { id: "mon-ad", title: "Монстера Адансона", heightCm: 35, price: 1190, art: "monstera", categories: ["Новое"] },
      { id: "fic-tin", title: "Фикус «Тинеке»", heightCm: 45, price: 1290, art: "ficus", categories: ["Новое", "Неприхотливые"] },
      { id: "zam-raven", title: "Замиокулькас «Равен»", heightCm: 55, price: 1690, art: "zamioculcas", categories: ["Новое", "Неприхотливые"] },
      { id: "cal-orb", title: "Калатея «Орбифолия»", heightCm: 45, price: 1590, art: "calathea", categories: ["Новое"] },
      { id: "sand", title: "Кашпо «Песок»", note: "Матовая керамика · 15 см", price: 650, art: "planter", categories: ["Кашпо"] },
      { id: "clay", title: "Кашпо «Терракота»", note: "Неглазурованная керамика · 20 см", price: 990, art: "planter", categories: ["Кашпо"] },
      { id: "graphite", title: "Кашпо «Графит»", note: "Матовая керамика · 17 см", price: 790, art: "planter", categories: ["Кашпо"] },
      { id: "ivory", title: "Кашпо «Молоко»", note: "Глазурованная керамика · 12 см", price: 590, art: "planter", categories: ["Кашпо"] },
    ],
  },
};

const price = (value: number) => `${value.toLocaleString("ru-RU", {
  minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
  maximumFractionDigits: 2,
})} ₽`;

const pickupTimes = [CAFE_DEMO.readyAt, "10:15", "10:30", "11:00"];
const cafeBonusVisits = 4;
const cafeBonusTarget = 6;
const plantBonusRate = 5;

function productNote(item: Item) {
  return item.heightCm === undefined ? item.note : `Высота без кашпо · ${item.heightCm} см`;
}

function selectedChoice(group: OptionGroup, selections: Selections) {
  return group.choices.find(choice => choice.id === selections[group.id]) ?? group.choices[0];
}

function cartVariant(item: Item, selections: Selections = {}): CartVariant {
  const choices = item.options?.map(group => selectedChoice(group, selections)) ?? [];
  return {
    key: [item.id, ...choices.map(choice => choice.id)].join(":"),
    item,
    unitPrice: item.price + choices.reduce((sum, choice) => sum + (choice.extra ?? 0), 0),
    summary: choices.map(choice => choice.summary).filter(Boolean).join(" · "),
  };
}

function ProductRow({
  item, count, line, onAdd, onRemove, onDetails, focusCart,
}: {
  item: Item;
  count: number;
  line?: CartLine;
  onAdd: () => void;
  onRemove?: () => void;
  onDetails?: () => void;
  focusCart?: () => void;
}) {
  const addButton = useRef<HTMLButtonElement>(null);
  const note = line?.summary || productNote(item);
  const label = line?.summary ? `${item.title}, ${line.summary}` : item.title;

  function removeOne() {
    if (count === 1) {
      if (focusCart) focusCart();
      else addButton.current?.focus({ preventScroll: true });
    }
    onRemove?.();
  }

  return (
    <div className="ys-row ys-row--art">
      <ProductArt kind={item.art} />
      <div className="ys-row-text">
        <div className="ys-catalog-product-title">
          <strong>{item.title}</strong>
          {onDetails && (
            <button type="button" className="ys-catalog-info" data-catalog-focus={`details-${item.id}`}
              onClick={onDetails} aria-label={`Состав: ${item.title}`}>
              <CircleAlert size={15} strokeWidth={1.7} aria-hidden="true" />
            </button>
          )}
        </div>
        {note && <small>{note}</small>}
        <b className="ys-price">{price(line ? line.unitPrice * count : item.price)}</b>
        {line && count > 1 && <small>{price(line.unitPrice)} за шт.</small>}
      </div>
      <div className="ys-stepper">
        {onRemove && <button type="button" onClick={removeOne} aria-label={`Убрать ${label}`}><Minus size={14} aria-hidden="true" /></button>}
        {count > 0 && <b aria-label={`Количество: ${count}`}>{count}</b>}
        <button type="button" ref={addButton} data-catalog-focus={`add-${item.id}`}
          onClick={onAdd} aria-label={`Добавить ${label}`}>
          <Plus size={14} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

function ProductHeading({ item }: { item: Item }) {
  return (
    <div className="ys-catalog-detail-heading">
      <ProductArt kind={item.art} />
      <div>
        <h3 tabIndex={-1}>{item.title}</h3>
        <p>{productNote(item)}</p>
      </div>
    </div>
  );
}

function DrinkOptions({
  item, selections, count, onSelect, onCount,
}: {
  item: Item;
  selections: Selections;
  count: number;
  onSelect: (group: string, choice: string) => void;
  onCount: (count: number) => void;
}) {
  return (
    <>
      <ProductHeading item={item} />
      {item.options?.map(group => (
        <label className="ys-catalog-field" key={group.id}>
          <span>{group.label}</span>
          <select aria-label={group.label} value={selectedChoice(group, selections).id}
            onChange={event => onSelect(group.id, event.target.value)}>
            {group.choices.map(choice => (
              <option key={choice.id} value={choice.id}>
                {choice.label}{choice.extra ? ` +${price(choice.extra)}` : ""}
              </option>
            ))}
          </select>
        </label>
      ))}
      <div className="ys-catalog-quantity">
        <span>Количество</span>
        <div className="ys-stepper">
          <button type="button" aria-label="Уменьшить количество" disabled={count === 1} onClick={() => onCount(count - 1)}>
            <Minus size={14} aria-hidden="true" />
          </button>
          <b>{count}</b>
          <button type="button" aria-label="Увеличить количество" onClick={() => onCount(count + 1)}>
            <Plus size={14} aria-hidden="true" />
          </button>
        </div>
      </div>
    </>
  );
}

function CafeCheckout({
  time, serving, onTime, onServing,
}: {
  time: string;
  serving: "Здесь" | "С собой";
  onTime: (time: string) => void;
  onServing: (serving: "Здесь" | "С собой") => void;
}) {
  return (
    <div className="ys-catalog-checkout">
      <div className="ys-catalog-serving" role="group" aria-label="Как подать заказ">
        {(["Здесь", "С собой"] as const).map(choice => (
          <button type="button" key={choice} aria-pressed={serving === choice} onClick={() => onServing(choice)}>{choice}</button>
        ))}
      </div>
      <label className="ys-catalog-field">
        <span>Время готовности</span>
        <select aria-label="Время готовности" value={time} onChange={event => onTime(event.target.value)}>
          {pickupTimes.map((slot, index) => (
            <option value={slot} key={slot}>{index === 0 ? `Скорее всего к ${slot}` : `К ${slot}`}</option>
          ))}
        </select>
      </label>
    </div>
  );
}

function LoyaltyDetails({ cafe, plantBonus }: { cafe: boolean; plantBonus: number }) {
  if (!cafe) {
    return (
      <>
        <h3 tabIndex={-1}>Карта покупателя</h3>
        <p className="ys-catalog-loyalty-description">{plantBonusRate}% от стоимости растений возвращается бонусами. За кашпо бонусы не начисляются.</p>
        <Strip title="Бонусы за этот заказ" note="От стоимости растений в корзине" value={price(plantBonus)} />
      </>
    );
  }

  return (
    <>
      <h3 tabIndex={-1}>Бонусная карта</h3>
      <p className="ys-catalog-loyalty-description">За каждый купленный кофе — отметка. Шестой кофе в подарок.</p>
      <div className="ys-catalog-stamps" aria-hidden="true">
        {Array.from({ length: cafeBonusTarget }, (_, index) => (
          <span key={index} data-collected={index < cafeBonusVisits}>
            {index < cafeBonusVisits ? <Check size={15} /> : <Coffee size={15} />}
          </span>
        ))}
      </div>
      <Strip title="Отметки" note="Две до подарка" value={`${cafeBonusVisits} из ${cafeBonusTarget}`} />
    </>
  );
}

export function CatalogDemo({ kind }: { kind: CatalogKind }) {
  const cafe = kind === "cafe";
  const brand = cafe ? CAFE_DEMO : SHOP_DEMO;
  const catalog = catalogs[kind];
  const [category, setCategory] = useState(catalog.categories[0]);
  const [query, setQuery] = useState("");
  const [cart, setCart] = useState<Record<string, CartLine>>({});
  const [view, setView] = useState<View>({ name: "catalog" });
  const [pickupTime, setPickupTime] = useState<string>(CAFE_DEMO.readyAt);
  const [serving, setServing] = useState<"Здесь" | "С собой">("С собой");
  const viewRef = useRef<HTMLDivElement>(null);
  const nextFocus = useRef<CatalogReturn | null>(null);

  useEffect(() => {
    const destination = nextFocus.current;
    const content = viewRef.current;
    if (!destination || !content) return;
    nextFocus.current = null;
    const trigger = [...content.querySelectorAll<HTMLButtonElement>("button[data-catalog-focus]")].find(
      button => button.dataset.catalogFocus === destination.focus && !button.disabled,
    );
    const footer = content.closest(".ys-screen")?.querySelector<HTMLButtonElement>('.ys-bar button[data-catalog-focus="open-cart"]:enabled');
    const target = trigger ?? (destination.focus === "open-cart" ? footer : null) ?? content.querySelector("h3") ?? content;
    if (content.parentElement) content.parentElement.scrollTop = destination.scrollTop;
    target.focus({ preventScroll: true });
  }, [view.name]);

  function changeQuantity(variant: CartVariant, delta: number) {
    setCart(previous => {
      const count = (previous[variant.key]?.count ?? 0) + delta;
      const next = { ...previous };
      if (count > 0) next[variant.key] = { ...variant, count };
      else delete next[variant.key];
      return next;
    });
  }

  function openView(next: View, focus: CatalogReturn = { focus: "", scrollTop: 0 }) {
    nextFocus.current = focus;
    setView(next);
  }

  function rememberCatalog(focus: string): CatalogReturn {
    return { focus, scrollTop: viewRef.current?.parentElement?.scrollTop ?? 0 };
  }

  function returnToCatalog(back?: CatalogReturn) {
    openView({ name: "catalog" }, back);
  }

  function startAgain() {
    setCart({});
    setPickupTime(CAFE_DEMO.readyAt);
    setServing("С собой");
    returnToCatalog();
  }

  const selected = Object.values(cart);
  const { quantity, total, plantSubtotal } = selected.reduce((sum, line) => {
    const lineTotal = line.unitPrice * line.count;
    return {
      quantity: sum.quantity + line.count,
      total: sum.total + lineTotal,
      plantSubtotal: sum.plantSubtotal + (line.item.heightCm === undefined ? 0 : lineTotal),
    };
  }, { quantity: 0, total: 0, plantSubtotal: 0 });
  const plantBonus = plantSubtotal * plantBonusRate / 100;
  const search = query.trim().toLocaleLowerCase("ru-RU");
  const shown = catalog.items.filter(item => item.categories.includes(category) && item.title.toLocaleLowerCase("ru-RU").includes(search));
  const searchLabel = cafe ? "Поиск по меню" : "Поиск по витрине";
  const pickup = cafe ? `${serving === "Здесь" ? "Готово" : "Самовывоз"} к ${pickupTime}` : "Самовывоз через 2 часа";
  const loyaltyTitle = cafe ? "Бонусная карта" : "Карта покупателя";
  const currentVariant = view.name === "customize" ? cartVariant(view.item, view.selections) : null;

  const action = (() => {
    switch (view.name) {
      case "catalog":
        return (
          <>
            <span className="ys-catalog-cart-total" role="status">
              {quantity ? <>Корзина · {quantity}<strong>{price(total)}</strong></> : "Корзина пуста"}
            </span>
            <button type="button" className="ys-bar-button is-on" data-catalog-focus="open-cart"
              disabled={!quantity} onClick={() => openView({ name: "cart", back: rememberCatalog("open-cart") })}>
              {quantity ? "Открыть корзину" : "Выберите позицию"}
            </button>
          </>
        );
      case "customize":
        if (!currentVariant) return undefined;
        return (
          <>
            <span className="ys-catalog-cart-total" role="status">
              К оплате<strong>{price(currentVariant.unitPrice * view.count)}</strong>
            </span>
            <button type="button" className="ys-bar-button is-on" onClick={() => {
              changeQuantity(currentVariant, view.count);
              returnToCatalog(view.back);
            }}>
              Добавить в корзину
            </button>
          </>
        );
      case "cart":
        return quantity ? (
          <button type="button" className="ys-bar-button is-on" onClick={() => openView({ name: "order" })}>Подтвердить заказ</button>
        ) : (
          <button type="button" className="ys-bar-button is-on" onClick={() => returnToCatalog(view.back)}>Вернуться в каталог</button>
        );
      case "order":
        return <button type="button" className="ys-bar-button is-on" onClick={startAgain}>Вернуться в каталог</button>;
      default:
        return undefined;
    }
  })();

  const back = "back" in view && (view.name !== "cart" || quantity > 0) ? view.back : null;
  const section = view.name === "customize" ? "Настройте напиток" : view.name === "ingredients" ? "Состав" : brand.section;

  return (
    <ScreenChrome name={brand.name} section={section} bar={action} bodyKey={view.name}
      backAction={back ? { label: "Вернуться в каталог", onClick: () => returnToCatalog(back) } : undefined}>
      <div className="ys-catalog-content" tabIndex={-1} ref={viewRef}>
        {view.name === "catalog" && (
          <>
            {cafe ? (
              <Promo kicker="Заберите без очереди" title={quantity ? `Будет готово к ${pickupTime}` : "Закажите заранее"}
                pill={quantity && pickupTime === CAFE_DEMO.readyAt ? CAFE_DEMO.wait : undefined} tone="cafe" />
            ) : (
              <Promo kicker="Самовывоз сегодня" title={quantity ? "Соберём за 2 часа" : "Выберите растение"}
                pill="бесплатно" tone="shop" />
            )}
            <label className="ys-search">
              <Search size={12} aria-hidden="true" />
              <input type="search" aria-label={searchLabel} placeholder={searchLabel} value={query} onChange={event => setQuery(event.target.value)} />
            </label>
            <div className="ys-chips ys-catalog-categories" role="group" aria-label="Категории каталога">
              {catalog.categories.map(label => (
                <button key={label} type="button" aria-pressed={label === category} onClick={() => setCategory(label)}>{label}</button>
              ))}
            </div>
            <button type="button" className="ys-strip ys-catalog-loyalty" aria-label={loyaltyTitle} data-catalog-focus="loyalty"
              onClick={() => openView({ name: "loyalty", back: rememberCatalog("loyalty") })}>
              <span>
                <strong>{loyaltyTitle}</strong>
                <small>{cafe ? "Шестой кофе в подарок" : `${plantBonusRate}% бонусами за растения`}</small>
              </span>
              <span className="ys-catalog-loyalty-value">
                <b>{cafe ? `${cafeBonusVisits} из ${cafeBonusTarget}` : price(plantBonus)}</b>
                <ChevronRight size={14} aria-hidden="true" />
              </span>
            </button>
            {shown.length ? shown.map(item => {
              const variants = selected.filter(line => line.item.id === item.id);
              const count = variants.reduce((sum, line) => sum + line.count, 0);
              const removable = variants.length === 1 ? variants[0] : undefined;
              return (
                <ProductRow key={item.id} item={item} count={count}
                  onAdd={() => {
                    if (item.options) {
                      openView({ name: "customize", item, count: 1, selections: {}, back: rememberCatalog(`add-${item.id}`) });
                    } else {
                      changeQuantity(cartVariant(item), 1);
                    }
                  }}
                  onRemove={removable ? () => changeQuantity(removable, -1) : undefined}
                  onDetails={item.ingredients ? () => openView({ name: "ingredients", item, back: rememberCatalog(`details-${item.id}`) }) : undefined} />
              );
            }) : <p className="ys-catalog-hint" role="status">В этой категории ничего не найдено</p>}
          </>
        )}
        {view.name === "ingredients" && (
          <>
            <ProductHeading item={view.item} />
            <ul className="ys-list ys-catalog-ingredients" aria-label="Состав продукта">
              {view.item.ingredients?.map(ingredient => <li key={ingredient}>{ingredient}</li>)}
            </ul>
          </>
        )}
        {view.name === "customize" && (
          <DrinkOptions item={view.item} count={view.count} selections={view.selections}
            onSelect={(group, choice) => setView({ ...view, selections: { ...view.selections, [group]: choice } })}
            onCount={count => setView({ ...view, count })} />
        )}
        {view.name === "loyalty" && <LoyaltyDetails cafe={cafe} plantBonus={plantBonus} />}
        {view.name === "cart" && (
          <>
            <h3 tabIndex={-1}>Ваш заказ</h3>
            {quantity ? (
              <>
                {cafe && <CafeCheckout time={pickupTime} serving={serving} onTime={setPickupTime} onServing={setServing} />}
                {selected.map(line => (
                  <ProductRow key={line.key} item={line.item} line={line} count={line.count}
                    onAdd={() => changeQuantity(line, 1)} onRemove={() => changeQuantity(line, -1)}
                    focusCart={() => viewRef.current?.querySelector("h3")?.focus({ preventScroll: true })} />
                ))}
                <Strip title="Итого" note={pickup} value={price(total)} />
                {!cafe && <Strip title="Бонусы за растения" note={`${plantBonusRate}% от стоимости растений`} value={price(plantBonus)} />}
              </>
            ) : <p className="ys-catalog-hint">Корзина пуста</p>}
          </>
        )}
        {view.name === "order" && (
          <div className="ys-catalog-receipt">
            <span><Check size={18} aria-hidden="true" /></span>
            <h3 tabIndex={-1}>Демо-заказ оформлен</h3>
            <p>{pickup}{cafe && ` · ${serving}`}</p>
            <div className="ys-line"><span>Код выдачи</span><strong>{cafe ? "1479" : "1624"}</strong></div>
            <Strip title="Итого" note="Демонстрационный заказ" value={price(total)} />
            {!cafe && <Strip title="Бонусы за растения" note={`${plantBonusRate}% от стоимости растений`} value={price(plantBonus)} />}
          </div>
        )}
      </div>
    </ScreenChrome>
  );
}
