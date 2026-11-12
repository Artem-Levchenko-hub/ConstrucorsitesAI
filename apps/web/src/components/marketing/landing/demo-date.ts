const demoTimeZone = "Europe/Moscow";
const calendar = new Intl.DateTimeFormat("en-GB", {
  timeZone: demoTimeZone,
  year: "numeric",
  month: "numeric",
  day: "numeric",
});
const clock = new Intl.DateTimeFormat("en-GB", {
  timeZone: demoTimeZone,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

// Keep date arithmetic independent of the server and visitor's time zones.
export function demoDay(now: number, offset = 0) {
  const parts = Object.fromEntries(calendar.formatToParts(now).map(({ type, value }) => [type, value]));
  return new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day) + offset));
}

export function demoClock(now: number) {
  return clock.format(now);
}

export function formatDemoDate(date: Date, options: Intl.DateTimeFormatOptions) {
  return new Intl.DateTimeFormat("ru-RU", { ...options, timeZone: "UTC" }).format(date);
}
