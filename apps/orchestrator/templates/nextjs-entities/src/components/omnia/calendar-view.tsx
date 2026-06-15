"use client";

import * as React from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "./empty-state";

/**
 * One scheduled record on the calendar — a single row mapped to a date + a
 * compact text tile. `date` decides which day it lands on (and, if it carries a
 * time, the «HH:MM» prefix); everything else is display. Accepts whatever the
 * field holds — an ISO string, an epoch number, or a Date — so the writer never
 * has to normalise the raw value first.
 */
export interface CalendarEvent {
  id: string;
  /** Raw value of the date field — string ("2026-06-15" / ISO), number (ms) or Date. */
  date: string | number | Date | null | undefined;
  title: React.ReactNode;
  /** One muted line under the title (a person, a place…). */
  subtitle?: React.ReactNode;
  /** Footer-left value, e.g. a price or a duration. */
  meta?: React.ReactNode;
  /** Small pill, e.g. a status or a category. */
  badge?: React.ReactNode;
  /** Hover-revealed controls (edit / delete) pinned top-right of the agenda row. */
  actions?: React.ReactNode;
  /** Extra text matched by the search box. */
  keywords?: string;
  onClick?: () => void;
}

export interface CalendarViewProps {
  events: CalendarEvent[];
  loading?: boolean;
  searchable?: boolean;
  searchPlaceholder?: string;
  /** Primary CTA inside the first-run empty state (no records at all). */
  emptyAction?: React.ReactNode;
  className?: string;
}

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const MONTHS = [
  "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
  "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
];

/** Local-day key — never crosses a timezone, so a record on the 15th always
 *  lands on the 15th regardless of the viewer's offset. */
function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/** Parse the raw field value into a LOCAL Date. A bare "YYYY-MM-DD" is read as
 *  local midnight (split, not `new Date(str)` which would treat it as UTC and
 *  shift the day back in negative-offset zones). Anything unparseable → null,
 *  so a stray value drops out instead of crashing the grid. */
function parseLocalDate(v: CalendarEvent["date"]): Date | null {
  if (v == null) return null;
  if (v instanceof Date) return Number.isNaN(v.getTime()) ? null : v;
  if (typeof v === "number") {
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const s = String(v).trim();
  if (!s) return null;
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (dateOnly) {
    const [, y, m, day] = dateOnly;
    return new Date(Number(y), Number(m) - 1, Number(day));
  }
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Whether the parsed value carried a wall-clock time worth showing. A bare
 *  date (or local midnight) reads as «all-day» — no «00:00» noise. */
function hasTime(v: CalendarEvent["date"], d: Date): boolean {
  if (typeof v === "string" && !v.includes("T") && !v.includes(":")) return false;
  return d.getHours() !== 0 || d.getMinutes() !== 0;
}

function timeLabel(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** Monday-first weekday index: JS Sunday=0 → 6, Monday=1 → 0. */
function mondayIndex(d: Date): number {
  return (d.getDay() + 6) % 7;
}

function eventText(e: CalendarEvent): string {
  const parts: string[] = [];
  if (typeof e.title === "string") parts.push(e.title);
  if (typeof e.subtitle === "string") parts.push(e.subtitle);
  if (e.keywords) parts.push(e.keywords);
  return parts.join(" ").toLowerCase();
}

interface Placed extends CalendarEvent {
  when: Date;
  timed: boolean;
}

/** A compact event chip inside a month cell — time prefix + truncated title,
 *  primary-tinted, click opens the record. */
function Chip({ ev }: { ev: Placed }) {
  const interactive = !!ev.onClick;
  return (
    <button
      type="button"
      onClick={
        interactive
          ? (e) => {
              // Don't also toggle the day cell underneath — open the record only.
              e.stopPropagation();
              ev.onClick?.();
            }
          : undefined
      }
      disabled={!interactive}
      title={typeof ev.title === "string" ? ev.title : undefined}
      className={cn(
        "flex w-full items-center gap-1 rounded-md border border-primary/20 bg-primary/10 px-1.5 py-0.5 text-left text-xs leading-tight text-foreground outline-none transition-colors",
        interactive && "hover:bg-primary/20 focus-visible:ring-2 focus-visible:ring-ring/40",
        !interactive && "cursor-default",
      )}
    >
      {ev.timed ? (
        <span className="shrink-0 font-semibold tabular-nums text-primary">{timeLabel(ev.when)}</span>
      ) : (
        <span className="size-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
      )}
      <span className="truncate font-medium">{ev.title}</span>
    </button>
  );
}

/** A rich agenda row — the mobile layout and the desktop selected-day panel
 *  share it. Title, optional subtitle/meta, time on the right, hover actions. */
function AgendaRow({ ev }: { ev: Placed }) {
  const interactive = !!ev.onClick;
  return (
    <div
      onClick={ev.onClick}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      onKeyDown={
        interactive
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                ev.onClick?.();
              }
            }
          : undefined
      }
      className={cn(
        "group hover-lift elev-1 relative flex items-start gap-3 rounded-xl border border-border bg-card p-3 outline-none",
        interactive && "cursor-pointer focus-visible:ring-[3px] focus-visible:ring-ring/40",
      )}
    >
      <div className="mt-0.5 flex w-12 shrink-0 flex-col items-center">
        {ev.timed ? (
          <span className="text-sm font-semibold tabular-nums text-primary">{timeLabel(ev.when)}</span>
        ) : (
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
            весь день
          </span>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <p className="font-semibold leading-tight tracking-tight">{ev.title}</p>
          {ev.badge != null ? (
            <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
              {ev.badge}
            </span>
          ) : null}
        </div>
        {ev.subtitle != null ? (
          <p className="truncate text-sm text-muted-foreground">{ev.subtitle}</p>
        ) : null}
        {ev.meta != null ? (
          <p className="mt-0.5 text-sm font-semibold tracking-tight tabular-nums">{ev.meta}</p>
        ) : null}
      </div>
      {ev.actions ? (
        <div
          onClick={(e) => e.stopPropagation()}
          className="absolute right-1.5 top-1.5 flex items-center gap-0.5 rounded-lg bg-background/85 p-0.5 opacity-0 shadow-sm backdrop-blur-sm transition-opacity group-hover:opacity-100 focus-within:opacity-100"
        >
          {ev.actions}
        </div>
      ) : null}
    </div>
  );
}

/**
 * A month-grid + agenda calendar — the schedule alternative to <DataTable> for
 * anything that lives on a date (записи, брони, события, встречи, смены, дедлайны).
 * Records land on their day; desktop shows the month grid (click a day to read
 * its full agenda below), mobile shows a grouped agenda list. Branded today-ring,
 * timed/all-day chips, month navigation, optional search, a loading skeleton and
 * a warm empty state.
 *
 *   <CalendarView
 *     events={bookings.map((b) => ({ id: b.id, date: b.startsAt, title: b.service,
 *       subtitle: b.client, meta: formatRub(b.price) }))}
 *   />
 */
export function CalendarView({
  events,
  loading,
  searchable,
  searchPlaceholder = "Поиск…",
  emptyAction,
  className,
}: CalendarViewProps) {
  const [query, setQuery] = React.useState("");

  const all = Array.isArray(events) ? events : [];
  const rawCount = all.length;

  // Parse + keep only datable records. The result is stable per event set, so
  // the grid never re-parses on a keystroke.
  const placed: Placed[] = React.useMemo(() => {
    const out: Placed[] = [];
    for (const e of all) {
      const when = parseLocalDate(e.date);
      if (!when) continue;
      out.push({ ...e, when, timed: hasTime(e.date, when) });
    }
    return out;
  }, [all]);

  const today = React.useMemo(() => new Date(), []);
  const todayKey = dayKey(today);

  // Default viewed month: the month of the event nearest to today (so a freshly
  // seeded app opens on data, not an empty November), else today's month.
  const initial = React.useMemo(() => {
    if (placed.length === 0) return { y: today.getFullYear(), m: today.getMonth() };
    let best = placed[0].when;
    let bestGap = Math.abs(best.getTime() - today.getTime());
    for (const p of placed) {
      const gap = Math.abs(p.when.getTime() - today.getTime());
      if (gap < bestGap) {
        bestGap = gap;
        best = p.when;
      }
    }
    return { y: best.getFullYear(), m: best.getMonth() };
  }, [placed, today]);

  const [cursor, setCursor] = React.useState(initial);
  // Re-centre when the data first arrives (initial is computed before fetch).
  const initRef = React.useRef(false);
  React.useEffect(() => {
    if (!initRef.current && placed.length > 0) {
      initRef.current = true;
      setCursor(initial);
    }
  }, [initial, placed.length]);

  const [selected, setSelected] = React.useState<string | null>(null);

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return placed;
    return placed.filter((e) => eventText(e).includes(q));
  }, [placed, query]);

  // Group by local-day key, each day's events sorted by time.
  const byDay = React.useMemo(() => {
    const map = new Map<string, Placed[]>();
    for (const e of filtered) {
      const k = dayKey(e.when);
      (map.get(k) ?? map.set(k, []).get(k)!).push(e);
    }
    for (const list of map.values()) {
      list.sort((a, b) => a.when.getTime() - b.when.getTime());
    }
    return map;
  }, [filtered]);

  // Month grid: leading blanks roll into the previous month, trailing into the
  // next, so the grid is always whole weeks (Monday-first).
  const cells = React.useMemo(() => {
    const first = new Date(cursor.y, cursor.m, 1);
    const lead = mondayIndex(first);
    const daysIn = new Date(cursor.y, cursor.m + 1, 0).getDate();
    const total = Math.ceil((lead + daysIn) / 7) * 7;
    return Array.from({ length: total }, (_, i) => new Date(cursor.y, cursor.m, 1 - lead + i));
  }, [cursor]);

  // Agenda (mobile + selected-day source): every day with events, soonest first.
  // Computed here — before the early returns — so the hook order stays stable.
  const agendaDays = React.useMemo(
    () =>
      [...byDay.keys()]
        .map((k) => ({ k, list: byDay.get(k)! }))
        .sort((a, b) => a.list[0].when.getTime() - b.list[0].when.getTime()),
    [byDay],
  );

  const noRecords = rawCount === 0;

  function go(delta: number) {
    setSelected(null);
    setCursor((c) => {
      const d = new Date(c.y, c.m + delta, 1);
      return { y: d.getFullYear(), m: d.getMonth() };
    });
  }
  function goToday() {
    setSelected(todayKey);
    setCursor({ y: today.getFullYear(), m: today.getMonth() });
  }

  if (loading) {
    return (
      <div className={cn("space-y-4", className)}>
        <div className="flex items-center justify-between">
          <Skeleton className="h-7 w-40 rounded-md" />
          <Skeleton className="h-9 w-28 rounded-md" />
        </div>
        <div className="grid grid-cols-7 gap-1.5">
          {Array.from({ length: 35 }).map((_, i) => (
            <Skeleton key={`cc-${i}`} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      </div>
    );
  }

  if (noRecords) {
    return (
      <EmptyState
        illustration="list"
        title="Пока пусто"
        description="Здесь появятся записи по датам, как только вы их добавите."
        action={emptyAction}
      />
    );
  }

  const selectedList = selected ? byDay.get(selected) ?? [] : [];

  const header = (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h3 className="text-lg font-semibold tracking-tight">
        {MONTHS[cursor.m]} {cursor.y}
      </h3>
      <div className="flex items-center gap-2">
        {searchable ? (
          <div className="relative w-40 sm:w-56">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={searchPlaceholder}
              className="pl-9"
            />
          </div>
        ) : null}
        <Button variant="outline" size="sm" onClick={goToday}>
          Сегодня
        </Button>
        <div className="flex items-center gap-1">
          <Button variant="outline" size="icon" onClick={() => go(-1)} aria-label="Предыдущий месяц">
            <ChevronLeft />
          </Button>
          <Button variant="outline" size="icon" onClick={() => go(1)} aria-label="Следующий месяц">
            <ChevronRight />
          </Button>
        </div>
      </div>
    </div>
  );

  return (
    <div className={cn("space-y-4", className)} data-omnia-calendar="" data-omnia-rows={rawCount}>
      {header}

      {/* Desktop / tablet — month grid */}
      <div className="hidden sm:block">
        <div className="mb-1.5 grid grid-cols-7 gap-1.5">
          {WEEKDAYS.map((w, i) => (
            <div
              key={w}
              className={cn(
                "px-1 text-center text-xs font-medium uppercase tracking-wide text-muted-foreground",
                i >= 5 && "text-muted-foreground/70",
              )}
            >
              {w}
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-1.5">
          {cells.map((d) => {
            const k = dayKey(d);
            const list = byDay.get(k) ?? [];
            const inMonth = d.getMonth() === cursor.m;
            const isToday = k === todayKey;
            const isSel = k === selected;
            const weekend = mondayIndex(d) >= 5;
            const shown = list.slice(0, 3);
            const extra = list.length - shown.length;
            const toggle = () => setSelected((s) => (s === k ? null : k));
            return (
              <div
                role="button"
                tabIndex={0}
                key={k}
                onClick={toggle}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    toggle();
                  }
                }}
                className={cn(
                  "flex min-h-24 cursor-pointer flex-col gap-1 rounded-lg border p-1.5 text-left align-top outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40",
                  inMonth ? "bg-card" : "bg-muted/20",
                  isSel
                    ? "border-primary/50 ring-2 ring-primary/30"
                    : "border-border/60 hover:border-border",
                  weekend && inMonth && "bg-muted/30",
                )}
              >
                <span
                  className={cn(
                    "flex h-6 w-6 items-center justify-center self-end rounded-full text-xs font-semibold tabular-nums",
                    isToday
                      ? "bg-primary text-primary-foreground"
                      : inMonth
                        ? "text-foreground"
                        : "text-muted-foreground/50",
                  )}
                >
                  {d.getDate()}
                </span>
                <div className="flex flex-col gap-1">
                  {shown.map((ev) => (
                    <Chip key={ev.id} ev={ev} />
                  ))}
                  {extra > 0 ? (
                    <span className="px-1 text-xs font-medium text-muted-foreground">
                      +{extra} ещё
                    </span>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>

        {/* Selected-day panel — full agenda for the clicked day */}
        {selected && selectedList.length > 0 ? (
          <div className="mt-4 rounded-2xl border border-border/60 bg-muted/30 p-4">
            <p className="mb-3 text-sm font-semibold tracking-tight">
              {(() => {
                const [y, m, dd] = selected.split("-").map(Number);
                const d = new Date(y, m, dd);
                return `${d.getDate()} ${MONTHS[d.getMonth()].toLowerCase()}, ${WEEKDAYS[mondayIndex(d)]}`;
              })()}
              <span className="ml-2 font-normal text-muted-foreground">
                {selectedList.length} {selectedList.length === 1 ? "запись" : "записей"}
              </span>
            </p>
            <div className="stagger space-y-2">
              {selectedList.map((ev) => (
                <AgendaRow key={ev.id} ev={ev} />
              ))}
            </div>
          </div>
        ) : null}
      </div>

      {/* Mobile — grouped agenda list (the grid is too cramped under ~640px) */}
      <div className="space-y-5 sm:hidden">
        {agendaDays.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border/60 px-4 py-10 text-center text-sm text-muted-foreground">
            Ничего не найдено
          </div>
        ) : (
          agendaDays.map(({ k, list }) => {
            const [y, m, dd] = k.split("-").map(Number);
            const d = new Date(y, m, dd);
            const isToday = k === todayKey;
            return (
              <div key={k}>
                <div className="mb-2 flex items-center gap-2">
                  <span
                    className={cn(
                      "flex h-7 min-w-7 items-center justify-center rounded-full px-1 text-sm font-semibold tabular-nums",
                      isToday ? "bg-primary text-primary-foreground" : "bg-muted text-foreground",
                    )}
                  >
                    {d.getDate()}
                  </span>
                  <span className="text-sm font-medium text-muted-foreground">
                    {MONTHS[d.getMonth()].toLowerCase()}, {WEEKDAYS[mondayIndex(d)]}
                  </span>
                </div>
                <div className="stagger space-y-2">
                  {list.map((ev) => (
                    <AgendaRow key={ev.id} ev={ev} />
                  ))}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
