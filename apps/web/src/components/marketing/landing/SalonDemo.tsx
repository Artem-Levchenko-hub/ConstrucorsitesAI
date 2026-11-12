"use client";

import { CheckCircle2, ChevronDown, Clock, Star } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Promo, ScreenChrome } from "./ScreenChrome";
import { SALON_DEMO } from "./demo-content";
import { demoClock, demoDay, formatDemoDate } from "./demo-date";
import "./salon-demo.css";

const SERVICES = [
  { name: "Стрижка", minutes: 60, price: 1800 },
  { name: "Окрашивание", minutes: 120, price: 3500 },
  { name: "Укладка", minutes: 40, price: 1200 },
  { name: "Уход для волос", minutes: 30, price: 900 },
];

const MASTERS = [
  {
    name: "Анна",
    fullName: "Анна Соколова",
    rating: "4,9",
    dayOff: 0,
    windows: [["10:00", 90], ["12:30", 120], ["14:30", 120], ["17:30", 60]],
    reviews: [
      { author: "Вера", quote: "Анна помогла подобрать форму, которую легко укладывать дома." },
      { author: "Лидия", quote: "Сначала обсудили длину, результат получился именно таким, как я хотела." },
    ],
  },
  {
    name: "Мария",
    fullName: "Мария Лебедева",
    rating: "4,8",
    dayOff: 1,
    windows: [["11:00", 120], ["13:30", 60], ["16:00", 120], ["18:30", 40]],
    reviews: [
      { author: "Екатерина", quote: "Мария подобрала оттенок и объяснила, как сохранить цвет." },
      { author: "Ирина", quote: "Укладка продержалась весь вечер." },
    ],
  },
  {
    name: "Елена",
    fullName: "Елена Орлова",
    rating: "5,0",
    dayOff: 2,
    windows: [["09:30", 60], ["12:00", 120], ["15:00", 90], ["17:00", 120]],
    reviews: [
      { author: "Ольга", quote: "Елена рассказала про каждый этап ухода. Волосы стали мягче." },
      { author: "Софья", quote: "Всё вовремя, стрижка получилась аккуратной." },
    ],
  },
] as const;

type Service = (typeof SERVICES)[number];
type Master = (typeof MASTERS)[number];
type Panel = "service" | "master" | "reviews";
type Choice = { day: number; slot?: string };
type Booking = { service: Service; master: Master; date: Date; slot: string };

const priceFormat = new Intl.NumberFormat("ru-RU");

function formatPrice(price: number) {
  return `${priceFormat.format(price)} ₽`;
}

function formatBookingDay(date: Date) {
  return formatDemoDate(date, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

function appointmentCheckTime(snapshot: number) {
  return Math.max(snapshot, Date.now());
}

export function SalonDemo({ now, onClockRefresh }: { now: number; onClockRefresh?: (now: number) => void }) {
  const [service, setService] = useState(SERVICES[0]);
  const [master, setMaster] = useState<Master>(MASTERS[0]);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [choice, setChoice] = useState<Choice | null>(null);
  const [booking, setBooking] = useState<Booking | null>(null);
  const serviceButton = useRef<HTMLButtonElement>(null);
  const masterButton = useRef<HTMLButtonElement>(null);
  const confirmationHeading = useRef<HTMLHeadingElement>(null);

  const clock = demoClock(now);
  const days = Array.from({ length: 5 }, (_, offset) => {
    const date = demoDay(now, offset);
    const slots = date.getUTCDay() === master.dayOff
      ? []
      : master.windows.flatMap(([start, availableMinutes]) =>
          availableMinutes >= service.minutes && (offset > 0 || start > clock) ? [start] : [],
        );
    return { date, day: date.getTime(), offset, slots };
  });
  const nearest = days.find(({ slots }) => slots.length > 0);
  const selectedDay = days.find(({ day }) => day === choice?.day) ?? nearest ?? days[0];
  const selectedSlot = selectedDay.day === choice?.day
    ? selectedDay.slots.find((slot) => slot === choice.slot)
    : selectedDay.slots[0];
  let nearestTitle = "Свободных окон пока нет";
  if (nearest) {
    const dayLabel = ["Сегодня", "Завтра"][nearest.offset]
      ?? formatDemoDate(nearest.date, { day: "numeric", month: "long" });
    nearestTitle = `${dayLabel} в ${nearest.slots[0]}`;
  }

  useEffect(() => {
    if (booking) confirmationHeading.current?.focus();
  }, [booking]);

  function togglePanel(next: Panel) {
    setPanel(panel === next ? null : next);
  }

  function selectService(next: Service) {
    if (next !== service) {
      setService(next);
      setChoice({ day: selectedDay.day });
    }
    setPanel(null);
    serviceButton.current?.focus();
  }

  function selectMaster(next: Master) {
    if (next !== master) {
      setMaster(next);
      setChoice({ day: selectedDay.day });
    }
    setPanel(null);
    masterButton.current?.focus();
  }

  function payOrReset() {
    if (booking) {
      setBooking(null);
      setChoice(null);
      setService(SERVICES[0]);
      setMaster(MASTERS[0]);
    } else if (selectedSlot) {
      const checkedAt = appointmentCheckTime(now);
      const today = demoDay(checkedAt).getTime();
      if (selectedDay.day < today || (selectedDay.day === today && selectedSlot <= demoClock(checkedAt))) {
        onClockRefresh?.(checkedAt);
        setChoice({ day: selectedDay.day });
        return;
      }
      setPanel(null);
      setBooking({ service, master, date: selectedDay.date, slot: selectedSlot });
    }
  }

  return (
    <ScreenChrome
      name={SALON_DEMO.name}
      section={SALON_DEMO.section}
      bodyKey={booking ? "confirmation" : "appointment"}
      bar={
        <button
          type="button"
          className="ys-bar-button ys-salon-pay is-on"
          disabled={!booking && !selectedSlot}
          onClick={payOrReset}
        >
          {booking ? "Начать заново" : "Оплатить"}
        </button>
      }
    >
      {booking ? (
        <div className="ys-salon-confirmation">
          <CheckCircle2 size={24} aria-hidden="true" />
          <h3 ref={confirmationHeading} tabIndex={-1}>Демо-запись оформлена</h3>
          <p>{booking.service.name} · {booking.master.fullName}</p>
          <p>{formatBookingDay(booking.date)} в {booking.slot}</p>
          <p>Оплата {formatPrice(booking.service.price)} и сообщение в MAX имитируются.</p>
        </div>
      ) : (
        <div className="ys-salon-form">
          <Promo kicker="Ближайшее окно" title={nearestTitle} pill={`${service.minutes} мин`} tone="salon" />

          <div className="ys-salon-pickers">
            <button
              ref={serviceButton}
              type="button"
              className="ys-line ys-salon-picker"
              aria-expanded={panel === "service"}
              onClick={() => togglePanel("service")}
            >
              <span>Услуга</span>
              <strong>{service.name} · {service.minutes} мин</strong>
              <ChevronDown size={14} aria-hidden="true" />
            </button>
            {panel === "service" && (
              <div className="ys-salon-options" role="group" aria-label="Услуги">
                {SERVICES.map((item) => (
                  <button key={item.name} type="button" aria-pressed={item === service} onClick={() => selectService(item)}>
                    <span><strong>{item.name}</strong><small>{item.minutes} мин</small></span>
                    <strong>{formatPrice(item.price)}</strong>
                  </button>
                ))}
              </div>
            )}

            <button
              ref={masterButton}
              type="button"
              className="ys-line ys-salon-picker"
              aria-expanded={panel === "master"}
              onClick={() => togglePanel("master")}
            >
              <span>Мастер</span>
              <strong>{master.name}</strong>
              <ChevronDown size={14} aria-hidden="true" />
            </button>
            {panel === "master" && (
              <div className="ys-salon-options" role="group" aria-label="Мастера">
                {MASTERS.map((item) => (
                  <button key={item.name} type="button" aria-pressed={item === master} onClick={() => selectMaster(item)}>
                    <strong>{item.fullName}</strong>
                    <small>{item.rating} из 5</small>
                  </button>
                ))}
              </div>
            )}

            <button
              type="button"
              className="ys-salon-reviews"
              aria-expanded={panel === "reviews"}
              onClick={() => togglePanel("reviews")}
            >
              <Star size={12} aria-hidden="true" />
              <span>{master.rating} из 5</span>
              <strong>Отзывы</strong>
              <ChevronDown size={14} aria-hidden="true" />
            </button>
            {panel === "reviews" && (
              <div className="ys-salon-review-list" role="group" aria-label={`Демо-отзывы · ${master.fullName}`}>
                <p className="ys-kicker ys-salon-kicker">Демо-отзывы</p>
                {master.reviews.map(({ author, quote }) => (
                  <figure key={author}>
                    <blockquote>{quote}</blockquote>
                    <figcaption>{author}</figcaption>
                  </figure>
                ))}
              </div>
            )}
          </div>

          <div className="ys-salon-calendar">
            <p className="ys-kicker ys-salon-kicker">
              {formatDemoDate(selectedDay.date, { month: "long", year: "numeric" })}
            </p>
            <div className="ys-week ys-salon-days" role="group" aria-label="Дата записи">
              {days.map(({ date, day, offset }) => (
                <button
                  key={day}
                  type="button"
                  aria-label={`${offset === 0 ? "Сегодня, " : ""}${formatBookingDay(date)}`}
                  aria-current={offset === 0 ? "date" : undefined}
                  aria-pressed={selectedDay.day === day}
                  onClick={() => {
                    if (selectedDay.day !== day) setChoice({ day });
                  }}
                >
                  <em>{formatDemoDate(date, { weekday: "short" })}</em>
                  {date.getUTCMonth() === selectedDay.date.getUTCMonth()
                    ? date.getUTCDate()
                    : formatDemoDate(date, { day: "numeric", month: "short" })}
                </button>
              ))}
            </div>
          </div>

          <div className="ys-salon-calendar">
            <p className="ys-kicker ys-salon-kicker">Время записи</p>
            {selectedDay.slots.length > 0 ? (
              <div className="ys-slots ys-salon-slots" role="group" aria-label="Время записи">
                {selectedDay.slots.map((slot) => (
                  <button
                    key={slot}
                    type="button"
                    aria-pressed={selectedSlot === slot}
                    className={selectedSlot === slot ? "is-on" : undefined}
                    onClick={() => setChoice({ day: selectedDay.day, slot })}
                  >
                    {slot}
                  </button>
                ))}
              </div>
            ) : (
              <p className="ys-salon-empty" role="status">На этот день нет свободного времени. Выберите другую дату.</p>
            )}
          </div>

          <div className="ys-strip ys-salon-total">
            <div><strong>{service.name}</strong><small>{service.minutes} мин</small></div>
            <div><small>Итого</small><b>{formatPrice(service.price)}</b></div>
          </div>
          <p className="ys-note ys-salon-note">
            <Clock size={12} aria-hidden="true" />Подтверждение о записи придёт в MAX
          </p>
          <p className="ys-salon-disclaimer">Оплата и запись в демо-режиме</p>
        </div>
      )}
    </ScreenChrome>
  );
}
