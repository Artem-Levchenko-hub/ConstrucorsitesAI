"use client";

import { CheckCircle2, ChevronDown, ChevronRight, Clock, Scissors, Star } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ScreenChrome } from "./ScreenChrome";
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
type Mode = "time" | "master";
type Slot = { master: Master; time: string };
type Day = { date: Date; offset: number; slots: Slot[] };
type Appointment = { service: Service; date: Date; slot: Slot };
type Stage =
  | { kind: "intro" | "services" | "schedule" }
  | { kind: "review" | "done"; appointment: Appointment };

const priceFormat = new Intl.NumberFormat("ru-RU");
const actionLabels = {
  intro: "Записаться",
  services: null,
  schedule: "Продолжить",
  review: "Подтвердить запись",
  done: "Начать заново",
};

function formatPrice(price: number) {
  return priceFormat.format(price) + " ₽";
}

function formatBookingDay(date: Date) {
  return formatDemoDate(date, { weekday: "long", day: "numeric", month: "long" });
}

function getSchedule(now: number, service: Service, masters: readonly Master[]): Day[] {
  const clock = demoClock(now);
  return Array.from({ length: 5 }, (_, offset) => {
    const date = demoDay(now, offset);
    const slots = masters.flatMap((master) => date.getUTCDay() === master.dayOff ? [] :
      master.windows.flatMap(([time, minutes]) =>
        minutes >= service.minutes && (offset > 0 || time > clock) ? [{ master, time }] : [],
      ),
    );
    slots.sort((first, second) => first.time.localeCompare(second.time));
    return { date, offset, slots };
  });
}

function isFutureAppointment(now: number, { date, slot }: Pick<Appointment, "date" | "slot">) {
  const today = demoDay(now).getTime();
  return date.getTime() > today || (date.getTime() === today && slot.time > demoClock(now));
}

function appointmentCheckTime(snapshot: number) {
  return Math.max(snapshot, Date.now());
}

function nearestTitle(day: Day) {
  const date = ["Сегодня", "Завтра"][day.offset]
    ?? formatDemoDate(day.date, { day: "numeric", month: "long" });
  return date + " в " + day.slots[0].time;
}

function MasterReviews({ master, expanded, onToggle }: { master: Master; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <button
        type="button"
        className="ys-salon-reviews"
        aria-label={"Отзывы — " + master.fullName}
        aria-expanded={expanded}
        onClick={onToggle}
      >
        <Star size={13} aria-hidden="true" />
        <span>{master.rating} из 5</span>
        <strong>Отзывы</strong>
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {expanded && (
        <div className="ys-salon-review-list" role="group" aria-label={"Демо-отзывы · " + master.fullName}>
          <h4>Демо-отзывы</h4>
          {master.reviews.map(({ author, quote }) => (
            <figure key={author}>
              <blockquote>{quote}</blockquote>
              <figcaption>{author}</figcaption>
            </figure>
          ))}
        </div>
      )}
    </>
  );
}

function AppointmentDetails({ appointment }: { appointment: Appointment }) {
  const { service, date, slot } = appointment;
  return (
    <dl className="ys-salon-details">
      <div><dt>Услуга</dt><dd>{service.name} · {service.minutes} мин</dd></div>
      <div><dt>Мастер</dt><dd>{slot.master.fullName}</dd></div>
      <div><dt>Дата и время</dt><dd>{formatBookingDay(date)} в {slot.time}</dd></div>
      <div><dt>Стоимость</dt><dd>{formatPrice(service.price)}</dd></div>
    </dl>
  );
}

export function SalonDemo({ now, onClockRefresh }: { now: number; onClockRefresh?: (now: number) => void }) {
  const [stage, setStage] = useState<Stage>({ kind: "intro" });
  const [service, setService] = useState<Service | null>(null);
  const [mode, setMode] = useState<Mode>("time");
  const [master, setMaster] = useState<Master | null>(null);
  const [masterPickerOpen, setMasterPickerOpen] = useState(false);
  const [reviewedMaster, setReviewedMaster] = useState<Master | null>(null);
  const [dayChoice, setDayChoice] = useState<number | null>(null);
  const [slotChoice, setSlotChoice] = useState<Slot | null>(null);
  const [clockFloor, setClockFloor] = useState(0);
  const [expired, setExpired] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const focusHeading = useRef(false);

  const clock = Math.max(now, clockFloor);
  const showMasters = mode === "master" && (!master || masterPickerOpen);
  const eligibleMasters = mode === "master" && master ? [master] : MASTERS;
  const days = stage.kind === "schedule" && service ? getSchedule(clock, service, eligibleMasters) : [];
  const nearest = days.find(({ slots }) => slots.length > 0);
  const selectedDay = days.find(({ date }) => date.getTime() === dayChoice) ?? nearest ?? days[0];
  const selectedSlot = selectedDay?.date.getTime() === dayChoice
    ? selectedDay.slots.find((slot) => slot.master === slotChoice?.master && slot.time === slotChoice.time)
    : undefined;
  const appointment = service && selectedDay && selectedSlot
    ? { service, date: selectedDay.date, slot: selectedSlot }
    : null;
  const reviewExpired = stage.kind === "review" && !isFutureAppointment(clock, stage.appointment);
  const actionLabel = stage.kind === "schedule" && showMasters ? null
    : reviewExpired ? "Выбрать другое время" : actionLabels[stage.kind];

  useEffect(() => {
    if (focusHeading.current) {
      heading.current?.focus();
      focusHeading.current = false;
    }
  }, [stage.kind]);

  function navigate(next: Stage) {
    focusHeading.current = true;
    setStage(next);
  }

  function goBack() {
    if (stage.kind === "schedule" && showMasters && master) {
      setMasterPickerOpen(false);
      setReviewedMaster(null);
      heading.current?.focus();
      return;
    }
    if (stage.kind === "services") navigate({ kind: "intro" });
    if (stage.kind === "schedule") navigate({ kind: "services" });
    if (stage.kind === "review") navigate({ kind: "schedule" });
  }

  function selectService(next: Service) {
    setService(next);
    if (slotChoice && !slotChoice.master.windows.some(([time, minutes]) => time === slotChoice.time && minutes >= next.minutes)) {
      setSlotChoice(null);
    }
    setReviewedMaster(null);
    setMasterPickerOpen(false);
    setExpired(false);
    navigate({ kind: "schedule" });
  }

  function selectMode(next: Mode) {
    if (mode === next) return;
    setMode(next);
    setMasterPickerOpen(false);
    setReviewedMaster(null);
  }

  function selectMaster(next: Master) {
    setMaster(next);
    setMasterPickerOpen(false);
    setReviewedMaster(null);
    heading.current?.focus();
  }

  function toggleReviews(target: Master) {
    setReviewedMaster(reviewedMaster === target ? null : target);
  }

  function chooseDay(day: number) {
    setDayChoice(day);
    if (selectedDay?.date.getTime() !== day) {
      setSlotChoice(null);
      setExpired(false);
    }
  }

  function chooseSlot(date: Date, slot: Slot) {
    const checkedAt = appointmentCheckTime(clock);
    if (!isFutureAppointment(checkedAt, { date, slot })) {
      refreshExpired(checkedAt);
      return;
    }
    setDayChoice(date.getTime());
    setSlotChoice(slot);
    setExpired(false);
  }

  function refreshExpired(checkedAt: number) {
    setClockFloor(checkedAt);
    onClockRefresh?.(checkedAt);
    setSlotChoice(null);
    setExpired(true);
  }

  function advanceAppointment(appointment: Appointment, kind: "review" | "done") {
    const checkedAt = appointmentCheckTime(clock);
    if (!isFutureAppointment(checkedAt, appointment)) {
      refreshExpired(checkedAt);
      if (stage.kind === "review") navigate({ kind: "schedule" });
      return;
    }
    navigate({ kind, appointment });
  }

  function startOver() {
    setService(null);
    setMode("time");
    setMaster(null);
    setMasterPickerOpen(false);
    setReviewedMaster(null);
    setDayChoice(null);
    setSlotChoice(null);
    setExpired(false);
    navigate({ kind: "intro" });
  }

  function primaryAction() {
    if (stage.kind === "intro") navigate({ kind: "services" });
    if (stage.kind === "schedule" && appointment) advanceAppointment(appointment, "review");
    if (stage.kind === "review") advanceAppointment(stage.appointment, "done");
    if (stage.kind === "done") startOver();
  }

  return (
    <ScreenChrome
      name={SALON_DEMO.name}
      section={SALON_DEMO.section}
      bodyKey={stage.kind}
      backAction={stage.kind === "intro" || stage.kind === "done" ? undefined : { label: "Назад", onClick: goBack }}
      bar={actionLabel && (
        <button
          type="button"
          className="ys-bar-button ys-salon-action is-on"
          disabled={stage.kind === "schedule" && !appointment}
          onClick={primaryAction}
        >
          {actionLabel}
        </button>
      )}
    >
      {stage.kind === "intro" && (
        <section className="ys-salon-step ys-salon-intro">
          <div className="ys-salon-studio">
            <Scissors size={30} aria-hidden="true" />
            <p>Студия красоты</p>
            <h3 ref={heading} tabIndex={-1}>Линия</h3>
            <p>Стрижки, окрашивание, укладка и уход за волосами</p>
          </div>
        </section>
      )}
      {stage.kind === "services" && (
        <section className="ys-salon-step">
          <h3 ref={heading} tabIndex={-1}>Выберите услугу</h3>
          <div className="ys-salon-options" role="group" aria-label="Услуги">
            {SERVICES.map((item) => (
              <button
                key={item.name}
                type="button"
                className="ys-salon-option"
                data-selected={item === service}
                aria-current={item === service ? "true" : undefined}
                onClick={() => selectService(item)}
              >
                <span><strong>{item.name}</strong><small>{item.minutes} мин</small></span>
                <strong>{formatPrice(item.price)}</strong>
                <ChevronRight size={14} aria-hidden="true" />
              </button>
            ))}
          </div>
        </section>
      )}
      {stage.kind === "schedule" && service && (
        <section className="ys-salon-step">
          <div className="ys-salon-heading">
            <h3 ref={heading} tabIndex={-1}>Дата и время</h3>
            <p>{service.name} · {service.minutes} мин · {formatPrice(service.price)}</p>
          </div>

          <div className="ys-salon-modes" role="group" aria-label="Способ записи">
            <button type="button" aria-pressed={mode === "time"} onClick={() => selectMode("time")}>По времени</button>
            <button type="button" aria-pressed={mode === "master"} onClick={() => selectMode("master")}>По мастеру</button>
          </div>

          {showMasters ? (
            <div className="ys-salon-options" role="group" aria-label="Мастера">
              <h4>Выберите мастера</h4>
              {MASTERS.map((item) => (
                <div key={item.name} className="ys-salon-master">
                  <button
                    type="button"
                    className="ys-salon-option"
                    data-selected={item === master}
                    aria-current={item === master ? "true" : undefined}
                    onClick={() => selectMaster(item)}
                  >
                    <strong>{item.fullName}</strong>
                    <ChevronRight size={14} aria-hidden="true" />
                  </button>
                  <MasterReviews master={item} expanded={reviewedMaster === item} onToggle={() => toggleReviews(item)} />
                </div>
              ))}
            </div>
          ) : (
            <>
              {mode === "master" && master && (
                <div className="ys-salon-master">
                  <div className="ys-salon-master-heading">
                    <strong>{master.fullName}</strong>
                    <button
                      type="button"
                      aria-label="Сменить мастера"
                      onClick={() => { setMasterPickerOpen(true); setReviewedMaster(null); heading.current?.focus(); }}
                    >
                      Сменить
                    </button>
                  </div>
                  <MasterReviews master={master} expanded={reviewedMaster === master} onToggle={() => toggleReviews(master)} />
                </div>
              )}
              {nearest && <div className="ys-salon-nearest"><Clock size={15} aria-hidden="true" /><span>Ближайшее окно</span><strong>{nearestTitle(nearest)}</strong></div>}
              {selectedDay && (
                <>
                  <div className="ys-salon-calendar">
                    <h4 className="ys-salon-month">{formatDemoDate(selectedDay.date, { month: "long" })}</h4>
                    <div className="ys-week ys-salon-days" role="group" aria-label="Дата записи">
                      {days.map(({ date, offset }) => {
                        const day = date.getTime();
                        return (
                          <button
                            key={day}
                            type="button"
                            aria-label={(offset === 0 ? "Сегодня, " : "") + formatBookingDay(date)}
                            aria-current={offset === 0 ? "date" : undefined}
                            aria-pressed={selectedDay.date.getTime() === day}
                            onClick={() => chooseDay(day)}
                          >
                            <em>{formatDemoDate(date, { weekday: "short" })}</em>
                            {date.getUTCMonth() === selectedDay.date.getUTCMonth()
                              ? date.getUTCDate()
                              : formatDemoDate(date, { day: "numeric", month: "short" })}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                  <div className="ys-salon-calendar">
                    <h4>Время записи</h4>
                    {expired && <p className="ys-salon-empty" role="status">Это время уже прошло. Выберите другое.</p>}
                    {selectedDay.slots.length > 0 ? (
                      <div className="ys-slots ys-salon-slots" role="group" aria-label="Время записи">
                        {selectedDay.slots.map((slot) => (
                          <button
                            key={slot.time + slot.master.name}
                            type="button"
                            aria-label={mode === "time" ? slot.time + ", " + slot.master.fullName : slot.time}
                            aria-pressed={selectedSlot === slot}
                            className={selectedSlot === slot ? "is-on" : undefined}
                            onClick={() => chooseSlot(selectedDay.date, slot)}
                          >
                            <strong>{slot.time}</strong>
                            {mode === "time" && <small>{slot.master.name}</small>}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <p className="ys-salon-empty" role="status">На этот день нет свободного времени. Выберите другую дату.</p>
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </section>
      )}
      {stage.kind === "review" && (
        <section className="ys-salon-step">
          <h3 ref={heading} tabIndex={-1}>Проверьте запись</h3>
          <AppointmentDetails appointment={stage.appointment} />
          <p className="ys-salon-note"><Clock size={13} aria-hidden="true" />Подтверждение о записи придёт в MAX</p>
          <p className="ys-salon-note">В демо-режиме запись и сообщение имитируются.</p>
          {reviewExpired && <p className="ys-salon-empty" role="status">Это время уже прошло. Выберите другое.</p>}
        </section>
      )}
      {stage.kind === "done" && (
        <section className="ys-salon-step">
          <div className="ys-salon-confirmation">
            <CheckCircle2 size={24} aria-hidden="true" />
            <h3 ref={heading} tabIndex={-1}>Демо-запись оформлена</h3>
            <p>Запись и сообщение в MAX имитируются.</p>
          </div>
          <AppointmentDetails appointment={stage.appointment} />
        </section>
      )}
    </ScreenChrome>
  );
}
