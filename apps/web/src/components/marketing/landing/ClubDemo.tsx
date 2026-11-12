"use client";

import { BookOpen, ChevronRight, Vote, X } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState, type FormEvent, type MouseEvent, type MouseEventHandler, type ReactNode } from "react";

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
const voteTitle = "Обсуждаемая книга на октябрь";
const choices = [...books, ownBook];
type VoteChoice = (typeof choices)[number];
type ClubPanel = { kind: "attendees"; title: string; attendees: readonly string[] } | { kind: "vote" };

function MeetingDate({ now, offset, short = false }: { now: number; offset: number; short?: boolean }) {
  const date = demoDay(now, offset);
  return (
    <time dateTime={date.toISOString().slice(0, 10)}>
      {formatDemoDate(date, { day: "numeric", month: short ? "short" : "long" })}
    </time>
  );
}

function ClubAction({ children, onClick, className = "" }: {
  children: ReactNode;
  onClick: MouseEventHandler<HTMLButtonElement>;
  className?: string;
}) {
  return (
    <button type="button" className={`ys-club-row ${className}`} aria-haspopup="dialog" onClick={onClick}>
      {children}
      <ChevronRight className="ys-club-chevron" size={15} aria-hidden="true" />
    </button>
  );
}

function ClubDialog({ title, children, onClose, subtitle, scrollLabel }: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  subtitle?: string;
  scrollLabel?: string;
}) {
  const titleId = useId();
  const close = useRef<HTMLButtonElement>(null);

  useLayoutEffect(() => close.current?.focus({ preventScroll: true }), []);

  return (
    <div className="ys-club-overlay">
      <div
        className="ys-club-dialog"
        role="dialog"
        aria-labelledby={titleId}
        onKeyDown={event => {
          if (event.key === "Escape" && !event.nativeEvent.isComposing) {
            event.preventDefault();
            event.stopPropagation();
            onClose();
            return;
          }
          if (event.key === "Tab") {
            const dialog = event.currentTarget;
            const radio = dialog.querySelector('input[type="radio"]:checked') ?? dialog.querySelector('input[type="radio"]');
            const stops = [...dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]')]
              .filter(element => element.getAttribute("type") !== "radio" || element === radio);
            const first = stops[0];
            const last = stops.at(-1);
            const edge = event.shiftKey ? first : last;
            if (first === last || document.activeElement === edge) {
              event.preventDefault();
              (event.shiftKey ? last : first)?.focus();
            }
          }
        }}
      >
        <div className="ys-club-dialog-header">
          <div>
            <h3 id={titleId}>{title}</h3>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button type="button" ref={close} className="ys-club-close" aria-label="Закрыть окно" onClick={onClose}>
            <X size={17} aria-hidden="true" />
          </button>
        </div>
        <div className="ys-club-dialog-body" role={scrollLabel ? "region" : undefined} aria-label={scrollLabel}
          tabIndex={scrollLabel ? 0 : undefined}>
          {children}
        </div>
      </div>
    </div>
  );
}

function BookVote({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [choice, setChoice] = useState<VoteChoice | null>(null);
  const [customTitle, setCustomTitle] = useState("");
  const [submittedBook, setSubmittedBook] = useState<string | null>(null);
  const result = useRef<HTMLParagraphElement>(null);
  const book = choice === ownBook ? customTitle.trim() : choice;

  useEffect(() => {
    if (submittedBook) result.current?.focus({ preventScroll: true });
  }, [submittedBook]);

  function submitVote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (book) setSubmittedBook(book);
  }

  if (!open) return null;

  return (
    <ClubDialog title={voteTitle} onClose={onClose}>
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
              <input type="text" value={customTitle} onChange={event => setCustomTitle(event.target.value)} maxLength={100} required />
            </label>
          )}
          <button className={`ys-action${book ? " is-on" : ""}`} type="submit" disabled={!book}>
            Проголосовать
          </button>
        </form>
      )}
    </ClubDialog>
  );
}

export function ClubDemo({ now }: { now: number }) {
  const [scheduleDate] = useState(now);
  const [selected, setSelected] = useState(0);
  const [joined, setJoined] = useState<ReadonlySet<string>>(() => new Set());
  const [panel, setPanel] = useState<ClubPanel | null>(null);
  const panelTrigger = useRef<HTMLButtonElement>(null);
  const meeting = upcoming[selected];
  const isJoined = joined.has(meeting.title);
  const attendees = isJoined ? [...meeting.attendees, "Вы"] : meeting.attendees;

  useLayoutEffect(() => {
    if (!panel && panelTrigger.current) {
      panelTrigger.current.focus({ preventScroll: true });
      panelTrigger.current = null;
    }
  }, [panel]);

  function openPanel(event: MouseEvent<HTMLButtonElement>, nextPanel: ClubPanel) {
    panelTrigger.current = event.currentTarget;
    setPanel(nextPanel);
  }

  function closePanel() {
    setPanel(null);
  }

  function toggleSignup() {
    setJoined(previous => {
      const next = new Set(previous);
      if (next.has(meeting.title)) next.delete(meeting.title);
      else next.add(meeting.title);
      return next;
    });
  }

  return (
    <div className="ys-club-shell">
      <div className="ys-club-chrome" inert={panel !== null}>
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
            <ClubAction onClick={event => openPanel(event, { kind: "attendees", title: meeting.title, attendees })}>
              <span className="ys-faces" aria-hidden="true"><span /><span /><span /></span>
              <strong>{attendees.length} участников</strong>
            </ClubAction>
            <h3 className="ys-kicker">Что будет на встрече</h3>
            <ul className="ys-list" role="list">
              {meeting.agenda.map(item => <li key={item}>{item}</li>)}
            </ul>
            <ClubAction className="ys-club-vote" onClick={event => openPanel(event, { kind: "vote" })}>
              <Vote size={18} aria-hidden="true" />
              <strong>{voteTitle}</strong>
            </ClubAction>
            <h3 className="ys-kicker">Прошлые встречи</h3>
            <div className="ys-club-archive">
              {past.map(({ title, offset, attendees }) => (
                <ClubAction key={title} onClick={event => openPanel(event, { kind: "attendees", title, attendees })}>
                  <span className="ys-club-past-title">
                    <strong>«{title}»</strong>
                    <small>{attendees.length} участников</small>
                  </span>
                  <MeetingDate now={scheduleDate} offset={offset} short />
                </ClubAction>
              ))}
            </div>
          </div>
        </ScreenChrome>
      </div>
      {panel?.kind === "attendees" && (
        <ClubDialog title="Участники встречи" subtitle={`«${panel.title}» · ${panel.attendees.length} участников`}
          scrollLabel="Список участников" onClose={closePanel}>
          <ul className="ys-list ys-club-attendees" role="list">
            {panel.attendees.map(name => <li key={name}>{name}</li>)}
          </ul>
        </ClubDialog>
      )}
      <BookVote open={panel?.kind === "vote"} onClose={closePanel} />
    </div>
  );
}
