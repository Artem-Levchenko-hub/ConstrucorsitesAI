import { describe, expect, it } from "vitest";

import { relativeDateLabel } from "@/lib/relative-date";

const now = new Date("2026-09-26T18:30:00");

describe("дата по-человечески", () => {
  it("называет сегодня и вчера с временем, ближние дни — словами", () => {
    expect(relativeDateLabel("2026-09-26T14:20:00", now)).toBe("сегодня в 14:20");
    expect(relativeDateLabel("2026-09-25T09:05:00", now)).toBe("вчера в 09:05");
    expect(relativeDateLabel("2026-09-24T09:05:00", now)).toBe("2 дня назад");
    expect(relativeDateLabel("2026-09-21T09:05:00", now)).toBe("5 дней назад");
  });

  it("дальние даты — числом и месяцем, прошлый год — с годом", () => {
    expect(relativeDateLabel("2026-09-17T12:00:00", now)).toBe("17 сентября");
    expect(relativeDateLabel("2025-12-31T12:00:00", now)).toBe("31 декабря 2025");
  });

  it("на пустую и битую дату строки нет вовсе", () => {
    expect(relativeDateLabel(null, now)).toBeNull();
    expect(relativeDateLabel("", now)).toBeNull();
    expect(relativeDateLabel("не дата", now)).toBeNull();
  });
});
