/**
 * Возможности платформы, показанные работающими фрагментами интерфейса.
 *
 * Список с галочками сообщает, что возможность заявлена. Маленький кусочек
 * настоящего экрана сообщает, что она существует: видно цену и остаток, видно
 * свободное время, видно способ оплаты, видно ответ на просьбу «добавь позицию».
 * Разница между «у нас есть онлайн-запись» и картинкой календаря со свободными
 * окнами — это разница между обещанием и доказательством.
 *
 * Фрагменты намеренно маленькие и неинтерактивные: их работа — узнаваемость за
 * полсекунды, а не второй продукт внутри витрины. Всё нарисовано разметкой,
 * поэтому остаётся чётким на любом экране и не тянет ни байта со стороны.
 */
import {
  CalendarClock,
  ChartColumn,
  Check,
  CreditCard,
  FileSpreadsheet,
  Handshake,
  Package,
  RefreshCw,
  Truck,
  UtensilsCrossed,
} from "lucide-react";

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
          ["Круассан", "в наличии", "190 ₽"],
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

      <Card title="Оплата внутри MAX" note="Приём платежей подключается через ЮKassa — на ваш договор и ваш счёт.">
        <div className="yl-cap-total">
          <span>К оплате</span>
          <strong>2 490 ₽</strong>
        </div>
        <div className="yl-cap-pay">
          <span className="is-on">
            <CreditCard size={13} />
            Карта
          </span>
          <span>СБП</span>
        </div>
        <div className="yl-cap-cta">Оплатить через ЮKassa</div>
      </Card>

      <Card title="Правки обычными словами" note="Не настройки и не поля: пишете сообщение — приложение меняется.">
        <div className="yl-cap-msg yl-cap-msg--you">
          Добавь тыквенный латте за 290 ₽ в раздел «Кофе»
        </div>
        <div className="yl-cap-msg yl-cap-msg--app">
          Готово. Изменение уже в приложении — проверьте в MAX.
        </div>
      </Card>

      <Card title="Бонусы и лояльность" note="Возвращают того клиента, который уже купил однажды.">
        <div className="yl-cap-loyal">
          <span>Бонусная карта</span>
          <strong>4 из 6</strong>
        </div>
        <div className="yl-cap-stamps">
          {[true, true, true, true, false, false].map((filled, i) => (
            <i key={i} className={filled ? "is-on" : undefined} />
          ))}
        </div>
        <p className="yl-cap-hint">Шестой кофе в подарок</p>
      </Card>

      <Card title="Заявки уходят в вашу систему" note="Заказ появляется там, где вы уже работаете, а остаток не расходится.">
        <div className="yl-cap-event">
          <span className="yl-cap-dot" />
          <div>
            <strong>Сделка создана</strong>
            <small>amoCRM · воронка «Заказы»</small>
          </div>
          <b>1 500 ₽</b>
        </div>
        <div className="yl-cap-event">
          <span className="yl-cap-dot yl-cap-dot--sync">
            <RefreshCw size={9} />
          </span>
          <div>
            <strong>Остаток обновлён</strong>
            <small>МойСклад</small>
          </div>
          <b className="yl-cap-ok">
            <Check size={12} />
          </b>
        </div>
      </Card>
    </div>
  );
}

/**
 * Значок у сервиса говорит, ЧТО он делает, а не как выглядит его логотип.
 *
 * Чужие логотипы мы не рисуем: товарный знак принадлежит его владельцу, и
 * похожая-но-своя картинка — это подделка знака, а не иллюстрация. Если
 * владелец получит официальные наборы логотипов, их можно будет подставить
 * сюда вместо значков, ничего больше не меняя.
 *
 * Значок при этом полезнее логотипа: строка «1С» ничего не говорит человеку,
 * который не работал с 1С, а «документы» — говорит. Одинаковая категория
 * получает одинаковый значок намеренно: так видно, что два сервиса
 * взаимозаменяемы и выбирать нужно один.
 */
const KIND = {
  pay: CreditCard,
  crm: Handshake,
  stock: Package,
  docs: FileSpreadsheet,
  menu: UtensilsCrossed,
  schedule: CalendarClock,
  delivery: Truck,
  stats: ChartColumn,
} as const;

/**
 * Настоящие интеграции платформы. Список взят из каталога провайдеров в коде,
 * а не придуман для красоты: называть чужой сервис, которого нет, — это обещание,
 * за которое потом отвечать перед человеком, уже заплатившим за подписку.
 */
export const INTEGRATIONS = [
  ["ЮKassa", "Приём оплаты", "pay"],
  ["amoCRM", "Заявки и сделки", "crm"],
  ["Битрикс24", "Заявки и сделки", "crm"],
  ["МойСклад", "Товары и остатки", "stock"],
  ["1С", "Товары, цены, документы", "docs"],
  ["iiko", "Меню и заказы", "menu"],
  ["r_keeper", "Меню, стоп-лист, заказы", "menu"],
  ["YCLIENTS", "Услуги и расписание", "schedule"],
  ["СДЭК", "Доставка и статусы", "delivery"],
  ["Яндекс Метрика", "Статистика посещений", "stats"],
] as const satisfies readonly (readonly [string, string, keyof typeof KIND])[];

export function Integrations() {
  return (
    <div className="yl-wrap yl-integrations">
      {INTEGRATIONS.map(([name, note, kind]) => {
        const Icon = KIND[kind];
        return (
          <div className="yl-integration" key={name}>
            <span className={`yl-integration-mark yl-integration-mark--${kind}`} aria-hidden="true">
              <Icon size={17} strokeWidth={2} />
            </span>
            <div>
              <strong>{name}</strong>
              <span>{note}</span>
            </div>
          </div>
        );
      })}
      <p className="yl-note yl-integrations-note">
        Подключение сервиса зависит от вашего тарифа и от вашего договора с этим
        сервисом. Генерация приложения сама по себе не включает приём платежей.
      </p>
    </div>
  );
}
