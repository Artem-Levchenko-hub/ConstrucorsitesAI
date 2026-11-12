/**
 * Неинтерактивные примеры возможностей: каталог, запись, оплата и управление.
 * Подключение магазина ЮKassa показано отдельно от примера клиентской оплаты.
 */
import { CreditCard, PackageX } from "lucide-react";

import "./payment-connection.css";

function Card({
  title,
  note,
  children,
}: {
  title: string;
  note: string;
  children: React.ReactNode;
}) {
  return (
    <article className="yl-cap">
      <div className="yl-cap-demo">{children}</div>
      <h3>{title}</h3>
      <p>{note}</p>
    </article>
  );
}

export function Capabilities() {
  return (
    <div className="yl-wrap yl-caps">
      <Card title="Каталог и заказы" note="Позиции, цены и остаток — клиент выбирает и оплачивает не выходя из MAX.">
        {[
          ["Капучино", "в наличии", "240 ₽"],
          ["Круассан с миндалём", "в наличии", "190 ₽"],
          ["Чизкейк", "осталось 3", "290 ₽"],
        ].map(([title, stock, price]) => (
          <div className="yl-cap-row" key={title}>
            <div>
              <strong>{title}</strong>
              <small>{stock}</small>
            </div>
            <b>{price}</b>
          </div>
        ))}
      </Card>

      <Card title="Онлайн-запись" note="Клиент сам находит свободное окно, а подтверждение приходит ему в MAX.">
        <div className="yl-cap-week">
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
        <div className="yl-cap-slots">
          <span>11:00</span>
          <span className="is-on">14:00</span>
          <span>17:30</span>
          <span>20:00</span>
        </div>
      </Card>

      <Card title="Оплата внутри MAX" note="Клиент оплачивает заказ через ЮKassa. Для приёма оплаты нужно подключить магазин.">
        <div className="yl-cap-total">
          <span>К оплате</span>
          <strong>2 490 ₽</strong>
        </div>
        <div className="yl-cap-pay">
          <span className="is-on">
            <CreditCard size={13} aria-hidden="true" />
            Карта
          </span>
          <span>СБП</span>
        </div>
        <div className="yl-cap-cta">Оплатить через ЮKassa</div>
      </Card>

      <Card title="Обновления после запуска" note="Добавляйте экраны и функции через чат. Проверьте изменения в предпросмотре и опубликуйте, когда всё готово.">
        <div className="yl-cap-msg yl-cap-msg--you">
          Добавь экран с сезонным меню и предзаказом
        </div>
        <div className="yl-cap-msg yl-cap-msg--app">
          Готово. Сезонное меню можно проверить в предпросмотре.
        </div>
      </Card>

      <Card title="Бонусы и лояльность" note="Бонусы и скидки помогают клиентам возвращаться за следующей покупкой.">
        <div className="yl-cap-loyal">
          <span>Бонусная система</span>
          <strong>4 из 6</strong>
        </div>
        <div className="yl-cap-stamps">
          {[true, true, true, true, false, false].map((filled, i) => (
            <i key={i} className={filled ? "is-on" : undefined} />
          ))}
        </div>
        <p className="yl-cap-hint">Шестой кофе в подарок</p>
      </Card>

      <Card title="Управление каталогом" note="Меняйте цены и наличие, скрывайте товары в панели управления.">
        <div className="yp-catalog-preview">
          <div className="yp-catalog-caption"><span>Каталог</span><span>Цена</span></div>
          <div className="yp-catalog-row">
            <div><strong>Капучино</strong><small>В наличии</small></div>
            <span>240 ₽</span>
          </div>
          <div className="yp-catalog-row">
            <div><strong>Круассан с миндалём</strong><small>В наличии</small></div>
            <span>190 ₽</span>
          </div>
          <div className="yp-catalog-unavailable">
            <PackageX size={12} aria-hidden="true" />
            <span>Чизкейка нет в наличии</span>
          </div>
        </div>
      </Card>
    </div>
  );
}

/**
 * На витрине предлагаем только подключение ЮKassa. Локальный знак и его
 * происхождение — в public/integrations/README.md; внешних загрузок здесь нет.
 */
export const INTEGRATIONS = [
  ["ЮKassa", "Приём оплаты", "yookassa", false],
] as const satisfies readonly (readonly [string, string, string, boolean])[];

export function Integrations() {
  const [name, , file] = INTEGRATIONS[0];

  return (
    <div className="yl-wrap">
      <article className="yp-payment" aria-label={`Подключение магазина ${name}`}>
        <div className="yp-payment-intro">
          {/* Локальный декоративный знак: название сервиса есть в тексте. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="yp-payment-logo" src={`/integrations/${file}.svg`} alt="" width={164} height={40} loading="lazy" decoding="async" />
          <p className="yp-payment-lead">Подключите свой магазин ЮKassa, чтобы клиенты могли оплачивать заказы.</p>
          <p className="yp-payment-note">Создание приложения само по себе не включает приём оплаты.</p>
        </div>
        <ol className="yp-payment-steps" aria-label="Как подключить приём оплаты" role="list">
          <li>
            <span className="yp-payment-number" aria-hidden="true">01</span>
            <div>
              <strong>Выберите свой магазин в ЮKassa</strong>
              <p>Можно использовать действующий магазин или зарегистрировать новый.</p>
            </div>
          </li>
          <li>
            <span className="yp-payment-number" aria-hidden="true">02</span>
            <div>
              <strong>Разрешите подключение в редакторе проекта</strong>
              <p>Нажмите «Подключить ЮKassa» и подтвердите доступ к магазину.</p>
            </div>
          </li>
          <li>
            <span className="yp-payment-number" aria-hidden="true">03</span>
            <div>
              <strong>Проверьте оплату перед запуском</strong>
              <p>Убедитесь, что клиент может перейти от заказа к оплате.</p>
            </div>
          </li>
        </ol>
      </article>
    </div>
  );
}
