import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as Dialog from "@radix-ui/react-dialog";

import { GuideLearning, LearningButton, ProductLearning } from "@/components/learning/ProductLearning";
import { learningForRoute, placeLearningCard } from "@/lib/product-learning";
import { MaxEditorLayout } from "@/components/max/MaxEditorLayout";
import type { Project } from "@/lib/api/types";

const route = vi.hoisted(() => ({ path: "/max", query: "" }));
vi.mock("next/navigation", () => ({ usePathname: () => route.path, useSearchParams: () => new URLSearchParams(route.query) }));

let root: Root;
let host: HTMLDivElement;
let reduced = false;
let targetRect = { left: 40, top: 90, width: 240, height: 48 };
let cardHeight = 300;
let cardWidth = 336;
let resizeObservers: { targets: Set<Element>; callback: () => void }[] = [];
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.useFakeTimers();
  const stored = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => { stored.set(String(key), String(value)); },
    removeItem: (key: string) => { stored.delete(key); },
    clear: () => stored.clear(),
    key: (index: number) => [...stored.keys()][index] ?? null,
    get length() { return stored.size; },
  } satisfies Storage);
  localStorage.clear();
  reduced = false;
  targetRect = { left: 40, top: 90, width: 240, height: 48 };
  cardHeight = 300;
  cardWidth = 336;
  resizeObservers = [];
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduced-motion") && reduced, addEventListener() {}, removeEventListener() {} }));
  route.path = "/max"; route.query = "";
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.hasAttribute("data-learning") || this.matches("section[id] h2")) return { ...targetRect, x: targetRect.left, y: targetRect.top, bottom: targetRect.top + targetRect.height, right: targetRect.left + targetRect.width, toJSON() {} };
    return { x: 0, y: 0, top: 0, left: 0, bottom: cardHeight, right: cardWidth, width: cardWidth, height: cardHeight, toJSON() {} };
  });
  HTMLElement.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("ResizeObserver", class {
    targets = new Set<Element>();
    constructor(public callback: () => void) { resizeObservers.push(this); }
    observe(element: Element) { this.targets.add(element); }
    disconnect() { this.targets.clear(); }
  });
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
async function settle() { await act(async () => vi.advanceTimersByTimeAsync(800)); }
async function click(label: string) {
  const button = [...document.querySelectorAll<HTMLButtonElement>("button")].find(item => item.textContent?.trim() === label || item.getAttribute("aria-label") === label);
  expect(button, label).toBeDefined(); await act(async () => button!.click()); await settle();
}
async function mount(userKey = "test-account-a", { hiddenSearch = false, onCreate = () => {}, startWelcome = true } = {}) {
  await act(async () => root.render(<><button data-learning="create-project" onClick={onCreate}>Создать приложение</button><div data-learning="projects-search" hidden={hiddenSearch}>Поиск</div><div data-learning="projects-help">Помощь</div><LearningButton /><ProductLearning userKey={userKey} /></>));
  await settle();
  if (startWelcome && document.querySelector('[data-learning-welcome]')) await click("Начать обучение");
}

function expectGuideLink(href: string) {
  const link = document.querySelector<HTMLAnchorElement>('.learning-card a[href^="/max/guide#"]');
  expect(link?.getAttribute("href")).toBe(href);
  expect(link?.textContent).toBe("Читать руководство в новой вкладке");
  expect(link?.target).toBe("_blank");
  expect(link?.relList.contains("noopener")).toBe(true);
}

describe("product learning", () => {
  it("starts on first cabinet visit, highlights real controls, and remembers dismissal per account", async () => {
    await mount();
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Создайте первое приложение");
    expect(document.querySelector(".learning-spotlight")).not.toBeNull();
    await click("Закрыть обучение");
    await act(async () => root.render(<ProductLearning key="new-visit" userKey="test-account-a" />)); await settle();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    await mount("test-account-b");
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it("can replay linear steps, skip absent targets, and return focus without performing app actions", async () => {
    await mount(); await click("Закрыть обучение");
    const launch = [...host.querySelectorAll<HTMLButtonElement>("button")].find(button => button.textContent?.trim() === "Обучение")!;
    launch.focus(); await click("Обучение");
    await click("Далее");
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Найдите своё приложение");
    expectGuideLink("/max/guide#project");
    expect(document.querySelector(".learning-card select")).toBeNull();
    expect(document.querySelector(".learning-counter")?.textContent).toBe("Шаг 2 из 3");
    await click("Назад");
    expect(document.querySelector(".learning-title")?.textContent).toBe("Создайте первое приложение");
    await click("Закрыть обучение");
    expect(document.activeElement).toBe(launch);
    expect(document.querySelector(".learning-spotlight")).toBeNull();
  });

  it("does not interrupt verification or another dialog and provides a manual tour in the guide", async () => {
    route.path = "/max/onboarding"; await mount();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    await act(async () => root.render(<><div role="dialog">Регистрация</div><ProductLearning userKey="test-account-c" /></>));
    route.path = "/max"; await act(async () => root.render(<><div role="dialog">Другое окно</div><ProductLearning key="cabinet" userKey="test-account-c" /></>)); await settle();
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    await act(async () => root.render(<><h2 id="guide-first" data-learning="guide-journey">Путь к запуску</h2><GuideLearning /></>)); await settle();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    await click("Перейти в режим обучения");
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Ваш путь к запуску");
  });

  it("closes a running tour on navigation and selects contextual content for editor dialogs", async () => {
    await mount(); route.path = "/max/test-project/catalog";
    await act(async () => root.render(<ProductLearning userKey="test-account-a" />)); await settle();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(learningForRoute("/max/test-project", "panel=publish")?.id).toBe("publish");
    expect(learningForRoute("/max/test-project", "data=details")?.id).toBe("details");
    expect(learningForRoute("/max/guide", "")?.id).toBe("guide");
    expect(learningForRoute("/max/onboarding", "")).toBeNull();
    expect(learningForRoute("/admin/max", "")).toBeNull();
  });

  it("keeps the card inside a narrow viewport when the target is near an edge", () => {
    for (const target of [null, { left: 370, top: 4, width: 80, height: 40 }, { left: -20, top: 720, width: 400, height: 200 }]) {
      const card = placeLearningCard(target, { width: 390, height: 780 }, { width: 358, height: 360 });
      expect(card.left).toBeGreaterThanOrEqual(16); expect(card.left + 358).toBeLessThanOrEqual(374);
      expect(card.top).toBeGreaterThanOrEqual(16); expect(card.top + 360).toBeLessThanOrEqual(764);
    }
  });

  it("waits for the cabinet to be ready and leaves a focused form uninterrupted", async () => {
    await act(async () => root.render(<><div data-learning-ready="false"><button data-learning="create-project">Создать</button><input autoFocus /></div><ProductLearning userKey="ready-account" /></>));
    await settle();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    await act(async () => host.querySelector('[data-learning-ready]')!.setAttribute("data-learning-ready", "true"));
    await settle();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    await act(async () => host.querySelector("input")!.blur());
    await settle();
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it("does not start over an alert dialog", async () => {
    await act(async () => root.render(<><div role="alertdialog">Подтвердите действие</div><button data-learning="create-project">Создать</button><ProductLearning userKey="alert-account" /></>));
    await settle();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.querySelector(".learning-spotlight")).toBeNull();
  });

  it("skips initially hidden controls and records completion without invoking app actions", async () => {
    const create = vi.fn();
    await mount("completion-account", { hiddenSearch: true, onCreate: create });
    expect(document.querySelector(".learning-counter")?.textContent).toBe("Шаг 1 из 2");
    expect(document.querySelector(".learning-card select")).toBeNull();
    await click("Далее");
    await click("Завершить");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(JSON.parse(localStorage.getItem("yleum:learning:v1:completion-account")!).tours.cabinet).toBe("completed");
    expect(create).not.toHaveBeenCalled();
    await mount("completion-account");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    await click("Обучение");
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it("traps focus, closes with Escape and restores the launch button", async () => {
    await mount(); await click("Закрыть обучение");
    const launch = host.querySelector<HTMLButtonElement>(".learning-launch")!;
    launch.focus(); await click("Обучение");
    const next = [...document.querySelectorAll<HTMLButtonElement>(".learning-actions button")].at(-1)!;
    next.focus();
    await act(async () => next.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true })));
    expect(document.activeElement).toBe(document.querySelector('[aria-label="Закрыть обучение"]'));
    await act(async () => document.activeElement!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })));
    await settle();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(launch);
  });

  it("replays inside an existing editor dialog and returns to it without changing form data", async () => {
    route.path = "/max/test-project"; route.query = "panel=publish";
    const parentChange = vi.fn();
    await act(async () => root.render(<><Dialog.Root open onOpenChange={parentChange}><Dialog.Portal><Dialog.Overlay /><Dialog.Content aria-describedby={undefined}><Dialog.Title>Публикация</Dialog.Title><LearningButton /><section data-learning="editor-panel"><input aria-label="Несохранённые данные" defaultValue="Сохранить мой черновик" /></section></Dialog.Content></Dialog.Portal></Dialog.Root><ProductLearning userKey="nested-account" /></>));
    await settle();
    const launch = document.querySelector<HTMLButtonElement>(".learning-launch")!;
    launch.focus(); await click("Обучение");
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(2);
    expect(document.querySelector(".learning-card")?.textContent).toContain("Проверьте готовность к публикации");
    expectGuideLink("/max/guide#publish");
    await act(async () => document.activeElement!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })));
    await settle();
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(parentChange).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(launch);
    expect(document.querySelector<HTMLInputElement>('[aria-label="Несохранённые данные"]')!.value).toBe("Сохранить мой черновик");
  });

  it("remeasures on scroll and gives an honest fallback when the current target disappears", async () => {
    await mount();
    expect((document.querySelector(".learning-spotlight") as HTMLElement).style.left).toBe("34px");
    targetRect = { left: 120, top: 360, width: 200, height: 40 };
    await act(async () => window.dispatchEvent(new Event("scroll")));
    expect((document.querySelector(".learning-spotlight") as HTMLElement).style.left).toBe("114px");
    await act(async () => { host.querySelector<HTMLElement>('[data-learning="create-project"]')!.hidden = true; });
    await settle();
    expect(document.querySelector(".learning-spotlight")).toBeNull();
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Элемент сейчас недоступен");
  });

  it("repositions the mounted card when its own height changes", async () => {
    targetRect = { left: 40, top: 650, width: 900, height: 48 };
    await mount();
    const panel = document.querySelector<HTMLElement>(".learning-card")!;
    const before = Number.parseFloat(panel.style.top);
    cardHeight = 500;
    await act(async () => { for (const observer of resizeObservers) if (observer.targets.has(panel)) observer.callback(); });
    expect(Number.parseFloat(panel.style.top)).toBeLessThan(before);
    expect(Number.parseFloat(panel.style.top) + 500).toBeLessThanOrEqual(window.innerHeight - 16);
  });

  it("scrolls to the selected real guide heading and respects reduced motion", async () => {
    reduced = true;
    await act(async () => root.render(<><div data-learning="guide-journey">Путь</div><section id="builder" className="guide-chapter"><h2>Редактор</h2></section><GuideLearning /></>));
    await settle(); await click("Перейти в режим обучения");
    await click("Далее");
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Редактор и проверка");
    expectGuideLink("/max/guide#builder");
    expect(vi.mocked(HTMLElement.prototype.scrollIntoView).mock.contexts.at(-1)).toBe(document.querySelector("#builder"));
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenLastCalledWith({ behavior: "auto", block: "start", inline: "nearest" });
  });

  it("shows a centered welcome before the first cabinet step and blocks background actions", async () => {
    const create = vi.fn();
    await mount("welcome-owner", { onCreate: create, startWelcome: false });
    expect(document.querySelector(".learning-card")?.textContent).toContain("Для начала пройдём короткое обучение");
    expect(document.querySelector(".learning-card")?.textContent).toContain("Создание проекта начнётся только после прохождения обучения. Во время обучения кнопки приложения неактивны");
    expectGuideLink("/max/guide#start");
    expect(document.querySelector(".learning-spotlight")).toBeNull();
    const createButton = host.querySelector<HTMLButtonElement>('[data-learning="create-project"]')!;
    expect(createButton.closest("[inert]")).not.toBeNull();
    await act(async () => createButton.click());
    expect(create).not.toHaveBeenCalled();
    await click("Начать обучение");
    expect(document.querySelector(".learning-title")?.textContent).toBe("Создайте первое приложение");
    expect(document.querySelector(".learning-card-top")?.textContent).toContain("Обучение");
    expect(document.querySelector(".learning-description")?.textContent).toBe("Начните с описания своей идеи.");
    expect(document.querySelector(".learning-spotlight")).not.toBeNull();
    await act(async () => createButton.click());
    expect(create).not.toHaveBeenCalled();
    await click("Закрыть обучение");
    expect(createButton.closest("[inert]")).toBeNull();
  });

  it("resumes an incomplete step instead of restarting and keeps completed v1 records through a dismissed replay", async () => {
    await mount("resume-owner"); await click("Далее"); await click("Закрыть обучение");
    await act(async () => root.unmount()); root = createRoot(host);
    await mount("resume-owner");
    expect(document.querySelector(".learning-card")).toBeNull();
    await click("Обучение");
    expect(document.querySelector(".learning-title")?.textContent).toBe("Найдите своё приложение");
    await click("Закрыть обучение");
    localStorage.setItem("yleum:learning:v1:legacy-owner", JSON.stringify({ version: 1, tours: { cabinet: "completed" } }));
    await mount("legacy-owner");
    expect(document.querySelector(".learning-card")).toBeNull();
    await click("Обучение"); await click("Закрыть обучение");
    expect(JSON.parse(localStorage.getItem("yleum:learning:v1:legacy-owner")!).tours.cabinet).toBe("completed");
  });

  it("ignores a stale open request belonging to another account", async () => {
    await mount("previous-owner"); await click("Закрыть обучение");
    await mount("current-owner"); await click("Закрыть обучение");
    await act(async () => window.dispatchEvent(new CustomEvent("yleum:learning:open", { detail: { scope: "product", userKey: "previous-owner", trigger: host.querySelector("button") } })));
    await settle();
    expect(document.querySelector(".learning-card")).toBeNull();
  });

  it("ignores an old detached launch element after the same account's page has changed", async () => {
    await mount("detached-owner"); await click("Закрыть обучение");
    const trigger = document.createElement("button");
    await act(async () => window.dispatchEvent(new CustomEvent("yleum:learning:open", { detail: { scope: "product", userKey: "detached-owner", trigger } })));
    await settle();
    expect(document.querySelector(".learning-card")).toBeNull();
  });

  it("does not run a stale automatic welcome timer after completion in another tab", async () => {
    await act(async () => root.render(<><button data-learning="create-project">Создать</button><ProductLearning userKey="external-completion-owner" /></>));
    localStorage.setItem("yleum:learning:v1:external-completion-owner", JSON.stringify({ version: 1, tours: { cabinet: "completed" } }));
    await act(async () => window.dispatchEvent(new StorageEvent("storage", { key: "yleum:learning:v1:external-completion-owner" })));
    await settle();
    expect(document.querySelector(".learning-card")).toBeNull();
  });

  it("leaves the measured guide heading unobscured at 1280x720 after linear navigation and height growth", async () => {
    vi.stubGlobal("innerWidth", 1280); vi.stubGlobal("innerHeight", 720);
    cardWidth = 358; cardHeight = 359;
    targetRect = { left: 293, top: 336.73, width: 850, height: 46.03 };
    vi.mocked(HTMLElement.prototype.scrollIntoView).mockImplementation(function (this: HTMLElement, options) {
      targetRect = { ...targetRect, top: this.matches(".guide-chapter") && typeof options === "object" && options.block === "start" ? 201.5 : 336.73 };
    });
    await act(async () => root.render(<><div data-learning="guide-journey">Путь</div>{["start", "account", "project", "builder", "settings"].map(id => <section key={id} id={id} className="guide-chapter"><h2 id={`${id}-title`}>{id}</h2></section>)}<GuideLearning /></>));
    await click("Перейти в режим обучения");
    for (let index = 0; index < 5; index++) await click("Далее");
    const assertGeometry = () => {
      const card = document.querySelector<HTMLElement>(".learning-card")!;
      const spotlight = document.querySelector<HTMLElement>(".learning-spotlight")!;
      const top = Number.parseFloat(card.style.top), targetTop = Number.parseFloat(spotlight.style.top), targetBottom = targetTop + Number.parseFloat(spotlight.style.height);
      expect(top + cardHeight <= targetTop - 16 || top >= targetBottom + 16).toBe(true);
      expect(top + cardHeight).toBeLessThanOrEqual(704);
    };
    expect(document.querySelector(".learning-title")?.textContent).toBe("Документы и каталог");
    assertGeometry();
    cardHeight = 400;
    await act(async () => { for (const observer of resizeObservers) if (observer.targets.has(document.querySelector(".learning-card")!)) observer.callback(); });
    assertGeometry();
  });

  it("offers contextual account and project tours but excludes guest and administrative pages", () => {
    for (const path of ["/account", "/account/security", "/billing", "/billing/plan", "/billing/transactions", "/max/project"]) {
      expect(learningForRoute(path, "")?.steps.length).toBeGreaterThan(0);
    }
    for (const section of ["catalog", "manage", "dashboard", "settings", "integrations", "publish"]) expect(learningForRoute(`/max/project/${section}`, "")?.id).toBe(section);
    for (const path of ["/register", "/login", "/max/onboarding", "/admin/max", "/max/project/preview"]) expect(learningForRoute(path, "")).toBeNull();
    expect(learningForRoute("/max/project", "panel=max")?.id).toBe("max");
    expect(learningForRoute("/max/project", "panel=services")?.id).toBe("services");
  });

  it.each([
    ["/billing/plan", "", "/max/guide#billing"],
    ["/max/project/catalog", "", "/max/guide#settings"],
    ["/max/project/integrations", "", "/max/guide#max-bot"],
    ["/max/project/integrations", "tab=services", "/max/guide#integrations"],
  ])("links the tour at %s with query %j to its lesson", async (path, query, href) => {
    route.path = path; route.query = query;
    await act(async () => root.render(<>
      <LearningButton />
      <main className="account-content" data-learning="project-section">Раздел</main>
      <ProductLearning userKey="lesson-owner" />
    </>));
    await click("Обучение");
    expectGuideLink(href);
  });

  it("links to the staff lesson when learning is opened from Team in management", async () => {
    route.path = "/max/project/manage";
    await act(async () => root.render(<>
      <div data-learning-context="staff-team"><LearningButton /></div>
      <main data-learning="project-section">Управление</main>
      <ProductLearning userKey="lesson-owner" />
    </>));
    await click("Обучение");
    expectGuideLink("/max/guide#staff");
  });

  it.each(["constructor", "toString", "__proto__"])("ignores unsupported prototype panel %s", value => {
    expect(learningForRoute("/max/project", `panel=${value}`)?.id).toBe("editor");
  });

  it.each([
    ['[data-testid="max-navigation-open"]', "Проекты и аккаунт"],
    ['[data-testid="max-mobile-preview-open"]', "Предпросмотр приложения"],
  ])("starts contextual learning from the real transient modal %s without changing its URL", async (selector, title) => {
    route.path = "/max/test-project";
    window.history.replaceState(null, "", route.path);
    await act(async () => root.render(<><MaxEditorLayout project={{ id: "test-project", name: "Проект" } as Project}
      navigation={<p>Ваши проекты</p>} preview={<p>Экран приложения</p>}>
      <input aria-label="Черновик редактора" defaultValue="Идея сохранена" /></MaxEditorLayout><ProductLearning userKey="transient-account" /></>));
    await act(async () => host.querySelector<HTMLButtonElement>(selector)!.click()); await settle();
    const parent = document.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(parent).not.toBeNull();
    const launch = parent.querySelector<HTMLButtonElement>(".learning-launch")!;
    await act(async () => launch.click()); await settle();
    expect(document.querySelector(".learning-title")?.textContent).toBe(title);
    expect(document.querySelector(".learning-spotlight")).not.toBeNull();
    expect(window.location.search).toBe("");
    await click("Закрыть обучение");
    expect(document.activeElement).toBe(launch);
    expect(host.querySelector<HTMLInputElement>('[aria-label="Черновик редактора"]')!.value).toBe("Идея сохранена");
  });
});
