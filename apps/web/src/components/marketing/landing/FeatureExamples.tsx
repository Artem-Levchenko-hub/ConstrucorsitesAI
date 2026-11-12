import { Check, Refrigerator, Ruler, ShowerHead, Sparkles, Wallet, CookingPot } from "lucide-react";

import { Promo, ScreenChrome } from "./ScreenChrome";
import "./feature-examples.css";

const numbers = new Intl.NumberFormat("ru-RU");

function rubles(value: number) {
  return `${numbers.format(value)} ₽`;
}

const AREA = 50;
const PRICE_PER_METER = 60;
const CLEANING_EXTRAS = [
  { name: "Духовка изнутри", price: 600, Icon: CookingPot },
  { name: "Холодильник изнутри", price: 500, Icon: Refrigerator },
];

export function EstimateScreen() {
  const cleaning = AREA * PRICE_PER_METER;
  const total = CLEANING_EXTRAS.reduce((sum, extra) => sum + extra.price, cleaning);

  return (
    <ScreenChrome
      name="Чисто дома"
      section="Калькулятор уборки"
      bar={<div className="ys-example-footer ys-example-footer--estimate"><span>Итого за уборку</span><strong>{rubles(total)}</strong></div>}
    >
      <div className="ys-feature-example ys-example-estimate">
        <Promo kicker="Расчёт стоимости" title="Поддерживающая уборка" tone="estimate" />
        <div>
          <h3>Квартира</h3>
          <dl className="ys-estimate-fields">
            <div><Ruler size={16} aria-hidden="true" /><dt>Площадь</dt><dd>{AREA} м²</dd></div>
            <div><ShowerHead size={16} aria-hidden="true" /><dt>Санузлы</dt><dd>1</dd></div>
          </dl>
        </div>
        <div className="ys-estimate-base">
          <div><h3>Базовая уборка</h3><p>{AREA} м² × {rubles(PRICE_PER_METER)}</p></div>
          <strong>{rubles(cleaning)}</strong>
        </div>
        <div>
          <h3>Дополнительные услуги</h3>
          <ul className="ys-estimate-extras">
            {CLEANING_EXTRAS.map(({ name, price, Icon }) => (
              <li key={name}>
                <span className="ys-estimate-extra-icon"><Icon size={17} aria-hidden="true" /></span>
                <div><strong>{name}</strong><small><Check size={11} aria-hidden="true" />Добавлено</small></div>
                <b>{rubles(price)}</b>
              </li>
            ))}
          </ul>
        </div>
        <div className="ys-estimate-included">
          <Sparkles size={16} aria-hidden="true" />
          <p><strong>В базовой уборке</strong><span>Полы, поверхности, кухня и один санузел. Средства и инвентарь включены.</span></p>
        </div>
      </div>
    </ScreenChrome>
  );
}

const COURSE_LESSONS = 4;
const COMPLETED_LESSONS = 2;

export function LessonScreen() {
  return (
    <ScreenChrome
      name="Кадр"
      section="Мини-курс по фотографии"
      bar={<div className="ys-example-footer ys-example-footer--lesson"><span>Следующий урок</span><strong>Свет и тень</strong></div>}
    >
      <div className="ys-feature-example ys-example-lesson">
        <div className="ys-lesson-progress">
          <h3>Съёмка на телефон</h3>
          <div><span>Пройдено</span><strong>{COMPLETED_LESSONS} из {COURSE_LESSONS}</strong></div>
          <progress aria-label="Пройдено уроков" value={COMPLETED_LESSONS} max={COURSE_LESSONS} />
        </div>
        <div className="ys-lesson-content">
          <p className="ys-example-label">Урок {COMPLETED_LESSONS}</p>
          <h3>Правило третей</h3>
          <div
            className="ys-lesson-viewfinder"
            role="img"
            aria-label="На сетке три на три главный объект расположен у верхнего правого пересечения линий."
          />
          <p>Расположите главный объект у пересечения линий сетки.</p>
        </div>
        <div>
          <p className="ys-example-label">Проверка</p>
          <h3>Где разместить главный объект?</h3>
          <ul className="ys-lesson-answers">
            <li>У края кадра</li>
            <li className="ys-lesson-correct"><Check size={15} aria-hidden="true" />У пересечения линий<strong>Верно</strong></li>
            <li>Всегда в центре</li>
          </ul>
        </div>
      </div>
    </ScreenChrome>
  );
}

const EXPENSE_CATEGORIES = [
  { id: "groceries", name: "Продукты" },
  { id: "cafes", name: "Кафе" },
  { id: "transport", name: "Транспорт" },
] as const;

const EXPENSE_DAYS = [
  {
    day: "Сегодня",
    entries: [
      { name: "Продукты у дома", category: "groceries", amount: 760 },
      { name: "Кофе с собой", category: "cafes", amount: 240 },
      { name: "Автобус", category: "transport", amount: 80 },
    ],
  },
  {
    day: "Вчера",
    entries: [
      { name: "Обед", category: "cafes", amount: 540 },
      { name: "Такси", category: "transport", amount: 380 },
    ],
  },
] as const;

export function ExpensesScreen() {
  const entries = EXPENSE_DAYS.flatMap(({ entries }) => [...entries]);
  const categories = EXPENSE_CATEGORIES.map(({ id, name }) => ({
    id,
    name,
    amount: entries.reduce((sum, entry) => entry.category === id ? sum + entry.amount : sum, 0),
  }));
  const total = categories.reduce((sum, category) => sum + category.amount, 0);

  return (
    <ScreenChrome name="По полочкам" section="Личный учёт расходов">
      <div className="ys-feature-example ys-example-expenses">
        <div className="ys-expense-total">
          <div><span>Расходы за 2 дня</span><strong>{rubles(total)}</strong></div>
          <Wallet size={24} aria-hidden="true" />
        </div>
        <div>
          <h3>По категориям</h3>
          <div className="ys-expense-chart" aria-hidden="true">
            {categories.map(({ id, amount }) => (
              <span key={id} className={`ys-expense-category--${id}`} style={{ flexGrow: amount }} />
            ))}
          </div>
          <dl className="ys-expense-legend">
            {categories.map(({ id, name, amount }) => (
              <div key={id}>
                <dt><span className={`ys-expense-category--${id}`} aria-hidden="true" />{name}</dt>
                <dd>{rubles(amount)}</dd>
              </div>
            ))}
          </dl>
        </div>
        {EXPENSE_DAYS.map(({ day, entries }) => (
          <div className="ys-expense-day" key={day}>
            <div><h3>{day}</h3><strong>{rubles(entries.reduce((sum, entry) => sum + entry.amount, 0))}</strong></div>
            <dl>
              {entries.map(({ name, category, amount }) => (
                <div key={name}>
                  <dt><span className={`ys-expense-category--${category}`} aria-hidden="true" />{name}</dt>
                  <dd>{rubles(amount)}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
    </ScreenChrome>
  );
}
