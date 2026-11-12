"use client";

import { Circle, CircleCheck, CookingPot, Refrigerator, Ruler, ShowerHead, Sparkles, Wallet } from "lucide-react";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { Promo, ScreenChrome } from "./ScreenChrome";
import "./feature-examples.css";

const numbers = new Intl.NumberFormat("ru-RU");
const fractions = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function rubles(value: number) {
  return `${(Number.isInteger(value) ? numbers : fractions).format(value)} ₽`;
}

const PRICE_PER_METER = 60;
const EXTRA_BATHROOM_PRICE = 350;
const CLEANING_EXTRAS = [
  { name: "Духовка изнутри", price: 600, Icon: CookingPot },
  { name: "Холодильник изнутри", price: 500, Icon: Refrigerator },
];

export function EstimateScreen() {
  const [areaInput, setAreaInput] = useState("50");
  const [bathrooms, setBathrooms] = useState(1);
  const [selectedExtras, setSelectedExtras] = useState(() => CLEANING_EXTRAS.map(({ name }) => name));
  const area = Number(areaInput);
  const validArea = Number.isInteger(area) && area >= 1 && area <= 999;
  const bathroomCost = (bathrooms - 1) * EXTRA_BATHROOM_PRICE;
  const cleaning = area * PRICE_PER_METER + bathroomCost;
  const total = CLEANING_EXTRAS.reduce((sum, { name, price }) => sum + (selectedExtras.includes(name) ? price : 0), cleaning);

  function toggleExtra(name: string) {
    setSelectedExtras(selected => selected.includes(name) ? selected.filter(item => item !== name) : [...selected, name]);
  }

  return (
    <ScreenChrome
      name="Чисто дома"
      section="Калькулятор уборки"
      bar={<div className="ys-example-footer ys-example-footer--estimate" role="status"><span>Итого за уборку</span><strong>{validArea ? rubles(total) : "—"}</strong></div>}
    >
      <div className="ys-feature-example ys-example-estimate">
        <Promo kicker="Расчёт стоимости" title="Поддерживающая уборка" tone="estimate" />
        <div>
          <h3>Квартира</h3>
          <div className="ys-estimate-fields">
            <label>
              <span><Ruler size={14} aria-hidden="true" />Площадь, м²</span>
              <input aria-label="Площадь, м²" aria-invalid={!validArea} type="number" inputMode="numeric" min={1} max={999} step={1}
                value={areaInput} onChange={event => setAreaInput(event.target.value)} />
            </label>
            <label>
              <span><ShowerHead size={14} aria-hidden="true" />Санузлы</span>
              <select aria-label="Санузлы" value={bathrooms} onChange={event => setBathrooms(Number(event.target.value))}>
                {[1, 2, 3, 4].map(count => <option key={count} value={count}>{count}</option>)}
              </select>
            </label>
          </div>
        </div>
        <div className="ys-estimate-base">
          <div>
            <h3>Базовая уборка</h3>
            {validArea ? <p>{area} м² × {rubles(PRICE_PER_METER)}{bathroomCost > 0 && <> + {rubles(bathroomCost)} за санузлы</>}</p>
              : <p>Укажите площадь от 1 до 999 м²</p>}
          </div>
          <strong>{validArea ? rubles(cleaning) : "—"}</strong>
        </div>
        <div>
          <h3>Дополнительные услуги</h3>
          <ul className="ys-estimate-extras">
            {CLEANING_EXTRAS.map(({ name, price, Icon }) => (
              <li key={name}>
                <label>
                  <span className="ys-estimate-extra-icon"><Icon size={17} aria-hidden="true" /></span>
                  <strong>{name}</strong>
                  <b>{rubles(price)}</b>
                  <input type="checkbox" aria-label={name} checked={selectedExtras.includes(name)} onChange={() => toggleExtra(name)} />
                </label>
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
const CURRENT_LESSON = 2;
const CORRECT_ANSWER = "У пересечения линий";
const ANSWERS = ["У края кадра", CORRECT_ANSWER, "Всегда в центре"];

export function LessonScreen() {
  const [answer, setAnswer] = useState<string | null>(null);
  const feedback = useRef<HTMLParagraphElement>(null);
  const solved = answer === CORRECT_ANSWER;
  const completed = solved ? CURRENT_LESSON : CURRENT_LESSON - 1;

  useEffect(() => { if (solved) feedback.current?.focus(); }, [solved]);

  return (
    <ScreenChrome
      name="Кадр"
      section="Мини-курс по фотографии"
      bar={<div className="ys-example-footer ys-example-footer--lesson"><span>Следующий урок</span><strong>Свет и тень</strong></div>}
    >
      <div className="ys-feature-example ys-example-lesson">
        <div className="ys-lesson-progress">
          <h3>Съёмка на телефон</h3>
          <div><span>Пройдено</span><strong>{completed} из {COURSE_LESSONS}</strong></div>
          <progress aria-label="Пройдено уроков" value={completed} max={COURSE_LESSONS} />
        </div>
        <div className="ys-lesson-content">
          <p className="ys-example-label">Урок {CURRENT_LESSON}</p>
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
          <fieldset className="ys-lesson-answers" disabled={solved}>
            <legend>Где разместить главный объект?</legend>
            {ANSWERS.map(option => (
              <button key={option} type="button" aria-pressed={answer === option}
                className={answer === option && solved ? "ys-lesson-correct" : undefined} onClick={() => setAnswer(option)}>
                {answer === option && solved ? <CircleCheck size={16} aria-hidden="true" /> : <Circle size={16} aria-hidden="true" />}
                {option}
              </button>
            ))}
          </fieldset>
          <p className="ys-lesson-feedback" role="status" ref={feedback} tabIndex={-1}>{answer && (solved ? "Верно. Урок засчитан." : "Попробуйте ещё.")}</p>
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

type ExpenseCategory = typeof EXPENSE_CATEGORIES[number]["id"];
type ExpenseInput = { name: string; category: ExpenseCategory; kopecks: number };
type Expense = ExpenseInput & { id: number };

const INITIAL_TODAY: Expense[] = [
  { id: 1, name: "Продукты у дома", category: "groceries", kopecks: 76000 },
  { id: 2, name: "Кофе с собой", category: "cafes", kopecks: 24000 },
  { id: 3, name: "Автобус", category: "transport", kopecks: 8000 },
];
const YESTERDAY: Expense[] = [
  { id: 4, name: "Обед", category: "cafes", kopecks: 54000 },
  { id: 5, name: "Такси", category: "transport", kopecks: 38000 },
];

function totalKopecks(entries: readonly Expense[]) {
  return entries.reduce((sum, entry) => sum + entry.kopecks, 0);
}

function ExpenseForm({ onSave }: { onSave: (entry: ExpenseInput) => void }) {
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [category, setCategory] = useState<ExpenseCategory>("groceries");
  const nameInput = useRef<HTMLInputElement>(null);
  const errorId = useId();
  const parsed = /^(\d{0,7})(?:[.,](\d{1,2}))?$/.exec(amount.trim());
  const kopecks = parsed ? Number(parsed[1]) * 100 + Number((parsed[2] ?? "").padEnd(2, "0")) : 0;
  const invalidAmount = amount.length > 0 && kopecks === 0;

  useEffect(() => { nameInput.current?.focus(); }, []);

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (name.trim() && kopecks > 0) onSave({ name: name.trim(), category, kopecks });
  }

  return (
    <form className="ys-feature-example ys-expense-form" onSubmit={save}>
      <h3>Новый расход</h3>
      <label>Название
        <input ref={nameInput} name="expense-name" value={name} maxLength={60} required onChange={event => setName(event.target.value)} />
      </label>
      <label>Сумма, ₽
        <input name="expense-amount" inputMode="decimal" value={amount} maxLength={10} required aria-invalid={invalidAmount}
          aria-describedby={invalidAmount ? errorId : undefined} onChange={event => setAmount(event.target.value)} />
      </label>
      {invalidAmount && <p id={errorId}>Введите сумму больше нуля, до двух знаков после запятой.</p>}
      <label>Категория
        <select name="expense-category" value={category} onChange={event => setCategory(event.target.value as ExpenseCategory)}>
          {EXPENSE_CATEGORIES.map(({ id, name }) => <option value={id} key={id}>{name}</option>)}
        </select>
      </label>
      <button type="submit" className="ys-bar-button is-on" disabled={!name.trim() || kopecks === 0}>Сохранить расход</button>
    </form>
  );
}

export function ExpensesScreen() {
  const [today, setToday] = useState(INITIAL_TODAY);
  const [editing, setEditing] = useState(false);
  const addButton = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef(false);
  const entries = [...today, ...YESTERDAY];
  const days = [{ day: "Сегодня", entries: today }, { day: "Вчера", entries: YESTERDAY }];
  const categories = EXPENSE_CATEGORIES.map(({ id, name }) => ({
    id,
    name,
    kopecks: totalKopecks(entries.filter(entry => entry.category === id)),
  }));
  const total = rubles(totalKopecks(entries) / 100);

  useEffect(() => {
    if (!editing && returnFocus.current) {
      addButton.current?.focus({ preventScroll: true });
      returnFocus.current = false;
    }
  }, [editing]);

  function addExpense(entry: ExpenseInput) {
    setToday(previous => [{ ...entry, id: previous.length + YESTERDAY.length + 1 }, ...previous]);
    setEditing(false);
  }

  return (
    <ScreenChrome name="По полочкам" section="Личный учёт расходов" bodyKey={editing ? "form" : "expenses"}
      backAction={editing ? { label: "К расходам", onClick: () => setEditing(false) } : undefined}
      bar={!editing && (
        <button ref={addButton} type="button" className="ys-bar-button ys-expense-add is-on" onClick={() => { returnFocus.current = true; setEditing(true); }}>
          Добавить расход
        </button>
      )}>
      {editing ? <ExpenseForm onSave={addExpense} /> : (
      <div className="ys-feature-example ys-example-expenses">
        <div className="ys-expense-total" role="status">
          <div><span>Расходы за 2 дня</span><strong>{total}</strong></div>
          <Wallet size={24} aria-hidden="true" />
        </div>
        <div>
          <h3>По категориям</h3>
          <div className="ys-expense-chart" aria-hidden="true">
            {categories.map(({ id, kopecks }) => (
              <span key={id} className={`ys-expense-category--${id}`} style={{ flexGrow: kopecks }} />
            ))}
          </div>
          <dl className="ys-expense-legend">
            {categories.map(({ id, name, kopecks }) => (
              <div key={id}>
                <dt><span className={`ys-expense-category--${id}`} aria-hidden="true" />{name}</dt>
                <dd>{rubles(kopecks / 100)}</dd>
              </div>
            ))}
          </dl>
        </div>
        {days.map(({ day, entries }) => (
          <div className="ys-expense-day" key={day}>
            <div><h3>{day}</h3><strong>{rubles(totalKopecks(entries) / 100)}</strong></div>
            <dl>
              {entries.map(({ id, name, category, kopecks }) => (
                <div key={id}>
                  <dt><span className={`ys-expense-category--${category}`} aria-hidden="true" />{name}</dt>
                  <dd>{rubles(kopecks / 100)}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
      )}
    </ScreenChrome>
  );
}
