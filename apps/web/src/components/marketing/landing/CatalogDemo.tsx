import { Check, Minus, Plus, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { CAFE_DEMO, SHOP_DEMO } from "./demo-content";
import { ProductArt, type ArtKind } from "./ProductArt";
import { Promo, ScreenChrome, Strip } from "./ScreenChrome";

type CatalogKind = "cafe" | "shop";
type View = "catalog" | "cart" | "order";
type Item = { id: string; title: string; note: string; price: number; art: ArtKind; categories: string[] };

const catalogs: Record<CatalogKind, { categories: string[]; items: Item[] }> = {
  cafe: {
    categories: ["Кофе", "Выпечка", "Десерты", "Чай"],
    items: [
      { id: "cap", title: "Капучино", note: "250 мл · на обычном молоке", price: 240, art: "cappuccino", categories: ["Кофе"] },
      { id: "flat", title: "Флэт уайт", note: "200 мл · больше кофе, меньше молока", price: 260, art: "flatwhite", categories: ["Кофе"] },
      { id: "cro", title: "Круассан с миндалём", note: "Испекли в 7:30", price: 190, art: "croissant", categories: ["Выпечка"] },
      { id: "cro-butter", title: "Классический круассан", note: "На сливочном масле", price: 160, art: "croissant", categories: ["Выпечка"] },
      { id: "che", title: "Чизкейк", note: "1 порция · нью-йорк", price: 290, art: "cheesecake", categories: ["Десерты"] },
      { id: "che-berry", title: "Малиновый чизкейк", note: "1 порция · с ягодным соусом", price: 320, art: "cheesecake", categories: ["Десерты"] },
      { id: "earl", title: "Эрл Грей", note: "350 мл · чёрный чай с бергамотом", price: 170, art: "tea", categories: ["Чай"] },
      { id: "jasmine", title: "Жасминовый чай", note: "350 мл · зелёный чай", price: 190, art: "tea", categories: ["Чай"] },
    ],
  },
  shop: {
    categories: ["Новое", "Неприхотливые", "Крупные", "Кашпо"],
    items: [
      { id: "mon", title: "Монстера", note: "Высота 75 см · в кашпо 17 см", price: 1400, art: "monstera", categories: ["Новое", "Крупные"] },
      { id: "fic", title: "Фикус", note: "Высота 60 см · в наличии", price: 980, art: "ficus", categories: ["Новое", "Неприхотливые", "Крупные"] },
      { id: "zam", title: "Замиокулькас", note: "В кашпо 15 см · осталось 3", price: 1150, art: "zamioculcas", categories: ["Новое", "Неприхотливые"] },
      { id: "cal", title: "Калатея", note: "Высота 40 см · в наличии", price: 890, art: "calathea", categories: ["Новое"] },
      { id: "sand", title: "Кашпо «Песок»", note: "Матовая керамика · 15 см", price: 650, art: "planter", categories: ["Кашпо"] },
      { id: "clay", title: "Кашпо «Терракота»", note: "Неглазурованная керамика · 20 см", price: 990, art: "planter", categories: ["Кашпо"] },
    ],
  },
};

const price = (value: number) => `${value.toLocaleString("ru-RU")} ₽`;

function ProductRow({ item, count, onChange, focusCart }: { item: Item; count: number; onChange: (delta: number) => void; focusCart?: () => void }) {
  const addButton = useRef<HTMLButtonElement>(null);

  function removeOne() {
    if (count === 1) {
      if (focusCart) focusCart();
      else addButton.current?.focus({ preventScroll: true });
    }
    onChange(-1);
  }

  return (
    <div className="ys-row ys-row--art">
      <ProductArt kind={item.art} />
      <div className="ys-row-text">
        <strong>{item.title}</strong>
        <small>{item.note}</small>
        <b className="ys-price">{price(item.price)}</b>
      </div>
      <div className="ys-stepper">
        {count > 0 && (
          <>
            <button type="button" onClick={removeOne} aria-label={`Убрать ${item.title}`}><Minus size={14} aria-hidden="true" /></button>
            <b aria-label={`Количество: ${count}`}>{count}</b>
          </>
        )}
        <button type="button" ref={addButton} onClick={() => onChange(1)} aria-label={`Добавить ${item.title}`}><Plus size={14} aria-hidden="true" /></button>
      </div>
    </div>
  );
}

export function CatalogDemo({ kind }: { kind: CatalogKind }) {
  const cafe = kind === "cafe";
  const brand = cafe ? CAFE_DEMO : SHOP_DEMO;
  const catalog = catalogs[kind];
  const [category, setCategory] = useState(catalog.categories[0]);
  const [query, setQuery] = useState("");
  const [cart, setCart] = useState<Record<string, number>>({});
  const [view, setView] = useState<View>("catalog");
  const viewRef = useRef<HTMLDivElement>(null);
  const focusNextView = useRef(false);

  useEffect(() => {
    if (!focusNextView.current) return;
    focusNextView.current = false;
    viewRef.current?.focus({ preventScroll: true });
  }, [view]);

  const changeQuantity = (id: string, delta: number) => setCart(previous => {
    const count = (previous[id] ?? 0) + delta;
    const next = { ...previous };
    if (count > 0) next[id] = count;
    else delete next[id];
    return next;
  });

  const openView = (next: View) => {
    focusNextView.current = true;
    setView(next);
  };

  const selected = catalog.items.filter(item => cart[item.id] > 0);
  const quantity = selected.reduce((count, item) => count + cart[item.id], 0);
  const total = selected.reduce((sum, item) => sum + item.price * cart[item.id], 0);
  const search = query.trim().toLocaleLowerCase("ru-RU");
  const shown = catalog.items.filter(item => item.categories.includes(category) && item.title.toLocaleLowerCase("ru-RU").includes(search));
  const searchLabel = cafe ? "Поиск по меню" : "Поиск по витрине";
  const pickup = cafe ? `Самовывоз к ${CAFE_DEMO.readyAt}` : "Самовывоз через 2 часа";
  const rows = (items: Item[]) => items.map(item => (
    <ProductRow key={item.id} item={item} count={cart[item.id] ?? 0} onChange={delta => changeQuantity(item.id, delta)}
      focusCart={view === "cart" ? () => viewRef.current?.focus({ preventScroll: true }) : undefined} />
  ));

  const action = view === "catalog" ? (
    <>
      <span className="ys-catalog-cart-total" role="status">
        {quantity ? <>Корзина · {quantity}<strong>{price(total)}</strong></> : "Корзина пуста"}
      </span>
      <button type="button" className="ys-bar-button is-on" disabled={!quantity} onClick={() => openView("cart")}>
        {quantity ? "Открыть корзину" : "Выберите позицию"}
      </button>
    </>
  ) : view === "cart" && quantity > 0 ? (
    <button type="button" className="ys-bar-button is-on" onClick={() => openView("order")}>
      Подтвердить заказ
    </button>
  ) : (
    <button type="button" className="ys-bar-button is-on" onClick={() => { setCart({}); openView("catalog"); }}>
      Вернуться в каталог
    </button>
  );

  return (
    <ScreenChrome name={brand.name} section={brand.section} bar={action} bodyKey={view}
      backAction={view === "cart" && quantity > 0 ? { label: "Вернуться в каталог", onClick: () => openView("catalog") } : undefined}>
      <div className="ys-catalog-content" tabIndex={-1} ref={viewRef}>
        {view === "catalog" && (
          <>
            {cafe
              ? <Promo kicker="Заберите без очереди" title={`Будет готово к ${CAFE_DEMO.readyAt}`} pill={CAFE_DEMO.wait} tone="cafe" />
              : <Promo kicker="Самовывоз сегодня" title="Соберём за 2 часа" pill="бесплатно" tone="shop" />}
            <label className="ys-search">
              <Search size={12} aria-hidden="true" />
              <input type="search" aria-label={searchLabel} placeholder={searchLabel} value={query} onChange={event => setQuery(event.target.value)} />
            </label>
            <div className="ys-chips ys-catalog-categories" role="group" aria-label="Категории каталога">
              {catalog.categories.map(label => (
                <button key={label} type="button" aria-pressed={label === category} onClick={() => setCategory(label)}>{label}</button>
              ))}
            </div>
            {shown.length ? rows(shown) : <p className="ys-catalog-hint" role="status">В этой категории ничего не найдено</p>}
            {cafe
              ? <Strip title="Бонусная карта" note="Шестой кофе в подарок" value="4 из 6" />
              : <Strip title="Карта покупателя" note="5% возвращается бонусами" value="640 ₽" />}
          </>
        )}
        {view === "cart" && (
          <>
            <h3>Ваш заказ</h3>
            {quantity ? <>{rows(selected)}<Strip title="Итого" note={pickup} value={price(total)} /></>
              : <p className="ys-catalog-hint">Корзина пуста</p>}
          </>
        )}
        {view === "order" && (
          <div className="ys-catalog-receipt">
            <span><Check size={18} aria-hidden="true" /></span>
            <h3>Демо-заказ оформлен</h3>
            <p>{pickup}</p>
            <div className="ys-line"><span>Код выдачи</span><strong>{cafe ? "1479" : "1624"}</strong></div>
            <Strip title="Итого" note="Демонстрационный заказ" value={price(total)} />
          </div>
        )}
      </div>
    </ScreenChrome>
  );
}
