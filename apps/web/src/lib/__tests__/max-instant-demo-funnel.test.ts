import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const source = (path: string) =>
  readFileSync(join(process.cwd(), "src", path), "utf8");

describe("MAX instant-demo funnel", () => {
  it("does not redirect a signed-in user to business verification before Studio", () => {
    const page = source("app/(app)/max/page.tsx");

    expect(page).toContain('if (session.isAnon) redirect("/max/register")');
    expect(page).not.toContain("session.emailVerifiedAt");
    expect(page).not.toContain("can_create_project");
  });

  it("promises one immediate demo and postpones external MAX work until launch", () => {
    const studio = source("components/max/MaxStudio.tsx");
    const launch = source("components/max/MaxLaunchPanel.tsx");

    expect(studio).toContain("Одна полноценная демо-сборка без верификации бизнеса и оплаты");
    expect(studio).toContain("Получить демо-приложение");
    expect(launch).toContain("Omnia подготовит:");
    expect(launch).toContain("Вы делаете в MAX Partner:");
    expect(launch).toContain("карточку и бота, модерацию");
  });
});
