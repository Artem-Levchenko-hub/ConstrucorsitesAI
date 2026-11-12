"use client";

import { MapPin, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { CAFE_DEMO } from "./demo-content";
import { ProductArt, type ArtKind } from "./ProductArt";
import { Promo, ScreenChrome, Strip } from "./ScreenChrome";

type OrderItem = { title: string; detail: string; price: number; art?: ArtKind };
type PastOrder = { date: string; dateTime: string; item: OrderItem };

const items: OrderItem[] = [
  { title: "Капучино", detail: "250 мл · 1 шт.", price: 240 },
  { title: "Круассан с миндалём", detail: "1 шт.", price: 190 },
];
const pastOrders: PastOrder[] = [
  { date: "24 сентября", dateTime: "2026-09-24", item: { title: "Флэт уайт", detail: "200 мл · 1 шт.", price: 260, art: "flatwhite" } },
  { date: "21 сентября", dateTime: "2026-09-21", item: { title: "Чизкейк нью-йорк", detail: "1 порция", price: 290, art: "cheesecake" } },
];

function OrderRow({ item }: { item: OrderItem }) {
  return (
    <div className={`ys-row${item.art ? " ys-row--art" : ""}`}>
      {item.art && <ProductArt kind={item.art} />}
      <div className="ys-row-text"><strong>{item.title}</strong><small>{item.detail}</small></div>
      <b>{item.price} ₽</b>
    </div>
  );
}

export function OrderStatusScreen({ allowRepeat = false }: { allowRepeat?: boolean }) {
  const [repeat, setRepeat] = useState<{ order: PastOrder; confirmed: boolean } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const returnTo = useRef<string | null>(null);
  const repeatButtons = useRef(new Map<string, HTMLButtonElement>());

  useEffect(() => {
    if (repeat) heading.current?.focus();
    else if (returnTo.current) {
      repeatButtons.current.get(returnTo.current)?.focus();
      returnTo.current = null;
    }
  }, [repeat]);

  if (repeat) {
    const back = () => setRepeat(null);
    return (
      <ScreenChrome name={CAFE_DEMO.name} section="Ваш заказ" bodyKey="repeat"
        backAction={repeat.confirmed ? undefined : { label: "К заказам", onClick: back }}
        bar={repeat.confirmed ? (
          <button type="button" className="ys-bar-button is-on" onClick={back}>К заказам</button>
        ) : (
          <button type="button" className="ys-bar-button is-on" onClick={() => setRepeat({ ...repeat, confirmed: true })}>Подтвердить повтор</button>
        )}>
        <h3 className="ys-order-repeat-title" ref={heading} tabIndex={-1}>
          {repeat.confirmed ? "Демо-заказ повторён" : "Повтор заказа"}
        </h3>
        <OrderRow item={repeat.order.item} />
        <Strip title="Итого" note="Самовывоз · ул. Садовая, 12" value={`${repeat.order.item.price} ₽`} />
        {repeat.confirmed && <p className="ys-note">Это демонстрационный заказ</p>}
      </ScreenChrome>
    );
  }

  return (
    <ScreenChrome name={CAFE_DEMO.name} section="Ваш заказ" bodyKey="status" bar={<b>Код выдачи · 1482</b>}>
      <Promo kicker="Заказ №1482" title="Можно забирать в 10:30" pill="2 мин" tone="status" />
      <ol className="ys-steps" aria-label="Статус заказа">
        {["Принят", "Готовим", "Можно забирать"].map((label, index) => (
          <li className={`ys-step${index < 2 ? " is-done" : ""}`} key={label} aria-current={index === 1 ? "step" : undefined}>
            <i aria-hidden="true" />
            <span>{label}</span>
          </li>
        ))}
      </ol>
      <p className="ys-kicker">В заказе</p>
      {items.map(item => <OrderRow key={item.title} item={item} />)}
      <p className="ys-note"><MapPin size={12} aria-hidden="true" />ул. Садовая, 12 · к 10:30</p>
      <Strip title="Оплачено" note="Картой через ЮKassa · 26 сентября" value="430 ₽" />
      <p className="ys-kicker">Прошлые заказы</p>
      {pastOrders.map(order => (
        <div key={order.dateTime}>
          <div className="ys-order-date">
            <time dateTime={order.dateTime}>{order.date}</time>
            {allowRepeat && (
              <button type="button" aria-label={`Повторить заказ от ${order.date}`} title="Повторить заказ"
                ref={element => {
                  if (element) repeatButtons.current.set(order.dateTime, element);
                  else repeatButtons.current.delete(order.dateTime);
                }}
                onClick={() => {
                  returnTo.current = order.dateTime;
                  setRepeat({ order, confirmed: false });
                }}>
                <RotateCcw size={13} aria-hidden="true" />
              </button>
            )}
          </div>
          <OrderRow item={order.item} />
        </div>
      ))}
    </ScreenChrome>
  );
}
