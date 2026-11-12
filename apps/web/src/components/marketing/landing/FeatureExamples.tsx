"use client";

import { Circle, CircleCheck, CookingPot, Refrigerator, Ruler, MessageSquare, ShowerHead, Sparkles, Star, TrainFront } from "lucide-react";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { Promo, ScreenChrome } from "./ScreenChrome";
import "./feature-examples.css";

const numbers = new Intl.NumberFormat("ru-RU");

function rubles(value: number) {
  return `${numbers.format(value)} ₽`;
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
const CORRECT_ANSWER = "Where is the train station?";
const ANSWERS = ["How much is the ticket?", CORRECT_ANSWER, "When does the train leave?"];

export function LessonScreen() {
  const [answer, setAnswer] = useState<string | null>(null);
  const feedback = useRef<HTMLParagraphElement>(null);
  const solved = answer === CORRECT_ANSWER;
  const completed = solved ? CURRENT_LESSON : CURRENT_LESSON - 1;

  useEffect(() => { if (solved) feedback.current?.focus(); }, [solved]);

  return (
    <ScreenChrome
      name="В пути"
      section="Английский для путешествий"
      bar={<div className="ys-example-footer ys-example-footer--lesson"><span>Следующий урок</span><strong>В гостинице</strong></div>}
    >
      <div className="ys-feature-example ys-example-lesson">
        <div className="ys-lesson-progress">
          <h3>Английский в поездке</h3>
          <div><span>Пройдено</span><strong>{completed} из {COURSE_LESSONS}</strong></div>
          <progress aria-label="Пройдено уроков" value={completed} max={COURSE_LESSONS} />
        </div>
        <div className="ys-lesson-content">
          <p className="ys-example-label">Урок {CURRENT_LESSON}</p>
          <div className="ys-lesson-phrase">
            <TrainFront size={24} aria-hidden="true" />
            <div><small>На вокзале</small><h3>Где находится вокзал?</h3></div>
          </div>
        </div>
        <div>
          <p className="ys-example-label">Проверка</p>
          <fieldset className="ys-lesson-answers" disabled={solved}>
            <legend>Как спросить по-английски?</legend>
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

const RATINGS = [1, 2, 3, 4, 5];

export function FeedbackScreen() {
  const formId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const focusAfterChange = useRef(false);
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState("");
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (focusAfterChange.current) {
      heading.current?.focus({ preventScroll: true });
      focusAfterChange.current = false;
    }
  }, [submitted]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (rating) {
      focusAfterChange.current = true;
      setSubmitted(true);
    }
  }

  function reset() {
    focusAfterChange.current = true;
    setRating(0);
    setComment("");
    setSubmitted(false);
  }

  return (
    <ScreenChrome name="Студия «Линия»" section="Отзыв о визите" bodyKey={submitted ? "review" : "form"}
      bar={submitted ? (
        <button type="button" className="ys-bar-button" onClick={reset}>Оценить ещё раз</button>
      ) : (
        <button type="submit" form={formId} className="ys-bar-button is-on" disabled={!rating}>Отправить отзыв</button>
      )}>
      {submitted ? (
        <div className="ys-feature-example ys-review-result">
          <div className="ys-review-visit">
            <CircleCheck size={22} aria-hidden="true" />
            <div>
              <h3 ref={heading} tabIndex={-1}>Демо-отзыв сохранён</h3>
              <p role="status">Оценка {rating} из 5</p>
            </div>
          </div>
          {comment.trim() && <p className="ys-review-comment">{comment.trim()}</p>}
        </div>
      ) : (
        <form id={formId} className="ys-feature-example ys-review-form" onSubmit={submit}>
          <div className="ys-review-visit">
            <MessageSquare size={22} aria-hidden="true" />
            <div>
              <h3 ref={heading} tabIndex={-1}>Как прошёл визит?</h3>
              <p>Стрижка и укладка</p>
            </div>
          </div>
          <fieldset>
            <legend>Ваша оценка</legend>
            <div className="ys-review-rating">
              {RATINGS.map(value => (
                <label key={value} data-filled={value <= rating || undefined}>
                  <input className="sr-only" type="radio" name="rating" value={value}
                    checked={rating === value} onChange={() => setRating(value)} required />
                  <Star size={20} aria-hidden="true" />
                  <span className="sr-only">Оценка {value} из 5</span>
                </label>
              ))}
            </div>
          </fieldset>
          <label className="ys-review-note">
            Комментарий <small>необязательно</small>
            <textarea name="review-comment" rows={3} maxLength={300} value={comment}
              placeholder="Расскажите о визите" onChange={event => setComment(event.target.value)} />
          </label>
        </form>
      )}
    </ScreenChrome>
  );
}
