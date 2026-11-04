import { Promo, ScreenChrome, Strip } from "./AppScreens";
import { CAFE_DEMO, type DemoScenario } from "./demo-content";

export type DemoOrderLine = { title: string; quantity: number; price: number };

export function HeroStaffScreen({
  scenario, brand, lines, slot, joined, participants,
}: {
  scenario: DemoScenario;
  brand: string;
  lines: DemoOrderLine[];
  slot: string;
  joined: boolean;
  participants: number;
}) {
  const total = lines.reduce((sum, line) => sum + line.quantity * line.price, 0);

  return (
    <ScreenChrome name={brand} section="Панель персонала" bar={<span>Пример экрана · доступен персоналу</span>}>
      {(scenario === "cafe" || scenario === "shop") && (
        <>
          <Promo kicker="Заказы на сегодня" title="Новый заказ №104" pill="9:41" tone={scenario} />
          <div className="ys-staff-card">
            <div className="ys-staff-heading"><strong>Самовывоз</strong><span>Оплачен</span></div>
            {lines.map(line => (
              <div className="ys-line" key={line.title}>
                <span>{line.title} × {line.quantity}</span>
                <strong>{(line.price * line.quantity).toLocaleString("ru-RU")} ₽</strong>
              </div>
            ))}
            <div className="ys-line ys-staff-total"><span>Итого</span><strong>{total.toLocaleString("ru-RU")} ₽</strong></div>
          </div>
          <div className="ys-line"><span>К выдаче</span><strong>{scenario === "cafe" ? CAFE_DEMO.readyAt : "11:41"}</strong></div>
          <Strip title="Статус оплаты" note="Пример оплаченного заказа" value="Оплачен" />
          <p className="ys-note">Персонал видит заказ в своей панели, клиент — подтверждение в MAX.</p>
        </>
      )}
      {scenario === "salon" && (
        <>
          <Promo kicker="Расписание мастера" title={`Сегодня в ${slot}`} pill="60 мин" tone="salon" />
          <div className="ys-staff-card">
            <p className="ys-kicker">Запись из приложения</p>
            <div className="ys-line"><span>Услуга</span><strong>Стрижка</strong></div>
            <div className="ys-line"><span>Мастер</span><strong>Анна</strong></div>
            <div className="ys-line"><span>Начало</span><strong>{slot}</strong></div>
            <div className="ys-line"><span>Длительность</span><strong>60 минут</strong></div>
          </div>
          <Strip title="Клиент записан" note="Подтверждение отправлено в MAX" value={slot} />
          <p className="ys-note">Клиент видит свою запись. Персонал — расписание и выбранную услугу.</p>
        </>
      )}
      {scenario === "club" && (
        <>
          <Promo kicker="Панель организатора" title="Записи на встречу" pill={`${participants} чел.`} tone="club" />
          <div className="ys-staff-card">
            <p className="ys-kicker">20 сентября · 19:00</p>
            <strong>Обсуждаем «Маленького принца»</strong>
            <div className="ys-line"><span>Участников</span><strong>{participants}</strong></div>
            <div className="ys-line"><span>Формат</span><strong>Онлайн</strong></div>
          </div>
          <Strip title={joined ? "Новая запись" : "Регистрация открыта"} note={joined ? "Участник записался из приложения" : "Участники записываются в MAX"} value={joined ? "+1" : String(participants)} />
          <p className="ys-note">Организатор видит записи, участник — программу и подтверждение участия.</p>
        </>
      )}
    </ScreenChrome>
  );
}
