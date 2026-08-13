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
    const guidance = source("lib/max-how-to.ts");

    expect(studio).toContain("Одна полноценная демо-сборка без верификации бизнеса и оплаты");
    expect(studio).toContain("Получить демо-приложение");
    expect(launch).toContain("MaxHowToDialog");
    expect(launch).toContain("howToGuide");
    expect(guidance).toContain("Аккаунт MAX Partner, бот и токен на этом этапе не нужны");
  });

  it("reserves the first generation before opening the auto-starting preview", () => {
    const studio = source("components/max/MaxStudio.tsx");
    const submitAt = studio.indexOf("await sendPrompt(project.id, prompt");
    const navigateAt = studio.indexOf("router.push(`/max/${project.id}`)");

    expect(submitAt).toBeGreaterThan(-1);
    expect(navigateAt).toBeGreaterThan(submitAt);
    expect(studio).toContain("idempotencyKey: `max-starter-${project.id}`");
    const preview = source("components/max/MaxLivePreview.tsx");
    expect(preview).toContain("if (deferInitialRuntimeStart) return");
    expect(preview).toContain(
      "enabled: runtimeRunning && !deferInitialRuntimeStart",
    );
  });
});
