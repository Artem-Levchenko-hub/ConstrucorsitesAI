"use client";

import { BookOpen, ChevronDown, Vote } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { ScreenChrome } from "./ScreenChrome";
import { CLUB_DEMO } from "./demo-content";
import { demoDay, formatDemoDate } from "./demo-date";
import "./club-demo.css";

const readers = [
  "Лида Чернильцева",
  "Никита Переплётов",
  "Мира Листова",
  "Антон Бумажников",
  "Соня Сказкина",
  "Федя Читалин",
  "Вера Строчкова",
  "Илья Закладкин",
  "Зоя Сноскина",
  "Тимур Абзацев",
  "Ася Тетрадкина",
  "Глеб Полочный",
  "Нина Зарифмова",
  "Павел Форзацев",
];

const upcoming = [
  {
    title: "Маленький принц",
    offset: 1,
    time: "19:00",
    topic: "Дружба и ответственность",
    agenda: [
      "Обсудим героев и любимые цитаты",
      "Поговорим о том, чему Принца учит Лис",
      "Пройдём короткий квиз по книге",
    ],
    attendees: readers,
  },
  {
    title: "451° по Фаренгейту",
    offset: 2,
    time: "19:00",
    topic: "Свобода думать и читать",
    agenda: [
      "Проследим, как меняется Монтэг",
      "Обсудим, почему в романе боятся книг",
      "Поделимся книгой, которую сохранили бы",
    ],
    attendees: readers.slice(1, 10),
  },
  {
    title: "Понедельник начинается в субботу",
    offset: 3,
    time: "18:30",
    topic: "Наука, магия и любимое дело",
    agenda: [
      "Поговорим о работе НИИЧАВО",
      "Вспомним, как волшебство соседствует с бытом",
      "Соберём список любимых находок авторов",
    ],
    attendees: readers.slice(3),
  },
] as const;

const past = [
  { title: "Мартин Иден", offset: -7, attendees: readers.slice(0, 12) },
  { title: "Три товарища", offset: -14, attendees: readers.slice(4) },
  { title: "Вино из одуванчиков", offset: -21, attendees: readers.slice(2, 10) },
];

const books = [
  "Над пропастью во ржи",
  "Цветы для Элджернона",
  "Гордость и предубеждение",
  "Норвежский лес",
  "Имя розы",
] as const;
const ownBook = "Свой вариант";
const choices = [...books, ownBook];
type VoteChoice = (typeof choices)[number];

function MeetingDate({ now, offset, short = false }: { now: number; offset: number; short?: boolean }) {
  const date = demoDay(now, offset);
  return (
    <time dateTime={date.toISOString().slice(0, 10)}>
      {formatDemoDate(date, { day: "numeric", month: short ? "short" : "long" })}
    </time>
  );
}

function Attendees({ attendees, children }: { attendees: readonly string[]; children: React.ReactNode }) {
  return (
    <details className="ys-club-disclosure">
      <summary className="ys-club-summary">
        {children}
        <ChevronDown className="ys-club-chevron" size={15} aria-hidden="true" />
      </summary>
      <ul className="ys-list ys-club-attendees" role="list" aria-label="Участники встречи">
        {attendees.map(name => <li key={name}>{name}</li>)}
      </ul>
    </details>
  );
}

function BookVote() {
  const [choice, setChoice] = useState<VoteChoice | null>(null);
  const [customTitle, setCustomTitle] = useState("");
  const [submittedBook, setSubmittedBook] = useState<string | null>(null);
  const result = useRef<HTMLParagraphElement>(null);
  const book = choice === ownBook ? customTitle.trim() : choice;

  useEffect(() => {
    if (submittedBook) result.current?.focus();
  }, [submittedBook]);

  function submitVote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (book) setSubmittedBook(book);
  }

  return (
    <details className="ys-club-disclosure ys-club-vote">
      <summary className="ys-club-summary">
        <Vote size={18} aria-hidden="true" />
        <strong>Обсуждаемая книга на октябрь</strong>
        <ChevronDown className="ys-club-chevron" size={15} aria-hidden="true" />
      </summary>
      <div role="status">
        {submittedBook && (
          <p className="ys-club-vote-result" ref={result} tabIndex={-1}>Голос принят. Вы выбрали «{submittedBook}».</p>
        )}
      </div>
      {!submittedBook && (
        <form className="ys-club-ballot" onSubmit={submitVote}>
          <fieldset aria-label="Выберите книгу">
            {choices.map(option => (
              <label className="ys-club-choice" key={option}>
                <input type="radio" name="book" checked={option === choice} onChange={() => setChoice(option)} />
                {option}
              </label>
            ))}
          </fieldset>
          {choice === ownBook && (
            <label className="ys-club-book-title">
              Название книги
              <input value={customTitle} onChange={event => setCustomTitle(event.target.value)} maxLength={100} required />
            </label>
          )}
          <button className={`ys-action${book ? " is-on" : ""}`} type="submit" disabled={!book}>
            Проголосовать
          </button>
        </form>
      )}
    </details>
  );
}

export function ClubDemo({ now }: { now: number }) {
  const [scheduleDate] = useState(now);
  const [selected, setSelected] = useState(0);
  const [joined, setJoined] = useState<ReadonlySet<string>>(() => new Set());
  const meeting = upcoming[selected];
  const isJoined = joined.has(meeting.title);
  const attendees = isJoined ? [...meeting.attendees, "Вы"] : meeting.attendees;

  function toggleSignup() {
    setJoined(previous => {
      const next = new Set(previous);
      if (next.has(meeting.title)) next.delete(meeting.title);
      else next.add(meeting.title);
      return next;
    });
  }

  return (
    <ScreenChrome
      name={CLUB_DEMO.name}
      section={CLUB_DEMO.section}
      bar={
        <>
          <span className="ys-club-signup-status" role="status">{isJoined ? "Вы записаны" : ""}</span>
          <button type="button" className={`ys-bar-button ys-club-signup${isJoined ? " is-done" : " is-on"}`} onClick={toggleSignup}>
            {isJoined ? "Отменить запись" : "Записаться на встречу"}
          </button>
        </>
      }
    >
      <div className="ys-club-demo">
        <h3 className="ys-kicker">Ближайшие встречи</h3>
        <div className="ys-club-dates" role="group" aria-label="Ближайшие встречи">
          {upcoming.map(({ title, offset }, index) => (
            <button key={title} type="button" onClick={() => setSelected(index)} aria-pressed={index === selected}>
              <MeetingDate now={scheduleDate} offset={offset} short />
              <span>{title}</span>
            </button>
          ))}
        </div>
        <div className="ys-hero-card ys-hero-card--club ys-club-meeting">
          <BookOpen size={21} aria-hidden="true" />
          <div>
            <strong>«{meeting.title}»</strong>
            <span className="ys-club-topic">{meeting.topic}</span>
            <span><MeetingDate now={scheduleDate} offset={meeting.offset} /> · {meeting.time} · онлайн</span>
          </div>
        </div>
        <Attendees key={meeting.title} attendees={attendees}>
          <span className="ys-faces" aria-hidden="true"><span /><span /><span /></span>
          <strong>{attendees.length} участников</strong>
        </Attendees>
        <h3 className="ys-kicker">Что будет на встрече</h3>
        <ul className="ys-list" role="list">
          {meeting.agenda.map(item => <li key={item}>{item}</li>)}
        </ul>
        <BookVote />
        <h3 className="ys-kicker">Прошлые встречи</h3>
        <div className="ys-club-archive">
          {past.map(({ title, offset, attendees }) => (
            <Attendees key={title} attendees={attendees}>
              <span className="ys-club-past-title">
                <strong>«{title}»</strong>
                <small>{attendees.length} участников</small>
              </span>
              <MeetingDate now={scheduleDate} offset={offset} short />
            </Attendees>
          ))}
        </div>
      </div>
    </ScreenChrome>
  );
}
