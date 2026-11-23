import { act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MaxEditorLayout } from "@/components/max/MaxEditorLayout";
import { PromptTextField } from "@/components/workspace/PromptTextField";
import { MaxSectionShell } from "@/components/max/MaxSectionShell";
import { useMaxWorkspaceDraftGuard } from "@/components/max/use-max-workspace-draft-guard";
import type { Project } from "@/lib/api/types";
import { OWNER_PROFILE_QUERY_KEY } from "@/lib/owner-chat-quota";

const boundary = vi.hoisted(() => ({ projects: vi.fn(), profile: vi.fn() }));
vi.mock("@/lib/api/projects", () => ({ listProjects: boundary.projects }));
vi.mock("@/lib/api/owner-profile", () => ({ getOwnerProfile: boundary.profile }));
vi.mock("@/lib/api/max-studio", () => ({ getMaxReadiness: async () => ({ items: [] }) }));
vi.mock("@/app/(auth)/actions", () => ({ logoutAction: vi.fn() }));
vi.mock("@/components/learning/ProductLearning", () => ({
  LearningButton: () => <button type="button">Обучение</button>,
}));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const project = { id: "qa", name: "Проект владельца" } as Project;
const applications = [
  { ...project, template: "max_miniapp" },
  { id: "other", name: "Другое приложение", template: "max_miniapp" },
  { id: "site", name: "Другой тип проекта", template: "landing" },
];
type Surface = "editor" | "section";

function DraftGuard({ surface }: { surface: Surface }) {
  useMaxWorkspaceDraftGuard(true, false, surface === "editor" ? "chat" : "project");
  return null;
}

describe("shared MAX project identity and navigation", () => {
  let host: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    document.body.replaceChildren();
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    boundary.projects.mockReset().mockResolvedValue(applications);
    boundary.profile.mockReset().mockResolvedValue({ id: "owner", email: "owner@example.test" });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    client.clear();
    vi.restoreAllMocks();
  });

  async function render(surface: Surface, name = project.name, dirty = false) {
    await act(async () => root.render(<QueryClientProvider client={client}>
      {dirty && <DraftGuard surface={surface} />}
      {surface === "editor"
        ? <MaxEditorLayout project={{ ...project, name }} email="owner@example.test" preview={<p>Живое превью</p>}>
          <input aria-label="Черновик" defaultValue="Не потерять" />
        </MaxEditorLayout>
        : <MaxSectionShell projectId={project.id} projectName={name} active="manage"
          eyebrow="" title="Управление" lead="Работа с приложением"><p>Содержимое раздела</p></MaxSectionShell>}
    </QueryClientProvider>));
  }

  function trigger() {
    return host.querySelector<HTMLButtonElement>('[data-testid="max-navigation-open"]')!;
  }

  async function open() {
    await act(async () => trigger().click());
    await act(async () => { await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).not.toBeNull()); });
  }

  it("uses the same brand, project subtitle and real disclosure in both shells", async () => {
    const identities = [];
    for (const surface of ["editor", "section"] as const) {
      await render(surface);
      const identity = host.querySelector('[data-testid="max-project-header-identity"]')!;
      identities.push({
        className: identity.className,
        brand: identity.querySelector('a[href="/max"]')?.innerHTML,
        project: identity.querySelector("strong")?.outerHTML,
        subtitle: identity.querySelector("small")?.outerHTML,
        trigger: trigger().className,
      });
      expect(trigger().getAttribute("aria-haspopup")).toBe("dialog");
      expect(trigger().getAttribute("aria-expanded")).toBe("false");
    }
    expect(identities[0]).toEqual(identities[1]);
    expect(boundary.projects).not.toHaveBeenCalled();
    expect(boundary.profile).not.toHaveBeenCalled();
  });

  it.each(["editor", "section"] as const)("opens real project and account destinations on %s", async surface => {
    await render(surface);
    await open();
    await act(async () => { await vi.waitFor(() => expect(document.querySelector('[data-testid="max-projects-scroll"]')).not.toBeNull()); });
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.querySelector('a[href="/max"]')?.textContent).toContain("Все проекты");
    expect(dialog.querySelector('a[href="/max/other"]')?.textContent).toContain("Другое приложение");
    expect(dialog.querySelector('a[href="/max/site"]')).toBeNull();
    expect(dialog.querySelector('a[href="/max/qa"]')?.getAttribute("aria-current")).toBe("page");
    for (const suffix of ["/manage", "/catalog", "/integrations", "/publish", "/dashboard"]) {
      expect(dialog.querySelector('a[href="/max/qa' + suffix + '"]')).not.toBeNull();
    }
    await act(async () => dialog.querySelector<HTMLButtonElement>('[data-testid="max-account-menu-trigger"]')!.click());
    for (const href of ["/account", "/account/security", "/billing", "/billing/transactions", "/billing/plan"]) {
      expect(dialog.querySelector('a[href="' + href + '"]')).not.toBeNull();
    }
    expect(dialog.querySelector('button[type="submit"]')?.textContent).toContain("Выйти");
    if (surface === "editor") {
      expect(boundary.profile).not.toHaveBeenCalled();
      expect(host.querySelector('a[href="/max/qa/publish"]')).not.toBeNull();
      expect(host.querySelector('a[href="/max/qa/integrations?tab=services"]')).not.toBeNull();
    } else {
      expect(boundary.profile).toHaveBeenCalledOnce();
      expect(host.querySelector('a[href="/account"]')).not.toBeNull();
    }
  });

  it.each(["editor", "section"] as const)("closes with Escape and returns focus on %s", async surface => {
    await render(surface);
    trigger().focus();
    await open();
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    await act(async () => { await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull()); });
    await vi.waitFor(() => expect(document.activeElement).toBe(trigger()));
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
    if (surface === "editor") expect(host.querySelector<HTMLInputElement>('[aria-label="Черновик"]')?.value).toBe("Не потерять");
  });

  it("tracks a panned iPhone keyboard and a resized Android viewport without applying the keyboard height twice", async () => {
    const viewport = Object.assign(new EventTarget(), { height: 844, offsetTop: 0, scale: 1 });
    vi.stubGlobal("visualViewport", viewport);
    vi.stubGlobal("innerHeight", 844);
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    try {
      await render("editor");
      const editor = host.querySelector<HTMLElement>(".max-editor")!;
      Object.assign(viewport, { height: 396.5, offsetTop: 31.5 });
      viewport.dispatchEvent(new Event("resize"));
      await vi.waitFor(() => expect(document.body.style.getPropertyValue("--max-editor-visual-height")).toBe("396.5px"));
      expect(document.body.style.getPropertyValue("--max-editor-visual-top")).toBe("31.5px");
      expect(editor.hasAttribute("data-keyboard-open")).toBe(true);
      vi.stubGlobal("innerHeight", 420);
      Object.assign(viewport, { height: 420, offsetTop: 0 });
      window.dispatchEvent(new Event("resize"));
      await vi.waitFor(() => expect(document.body.style.getPropertyValue("--max-editor-visual-height")).toBe("420px"));
      expect(editor.hasAttribute("data-keyboard-open")).toBe(false);
      Object.assign(viewport, { height: 840, scale: 2 });
      viewport.dispatchEvent(new Event("scroll"));
      await vi.waitFor(() => expect(document.body.style.getPropertyValue("--max-editor-visual-height")).toBe(""));
      expect(document.body.style.getPropertyValue("--max-editor-visual-top")).toBe("");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("redocks on focus and blur inside the iOS field even when Safari omits the final resize", async () => {
    const viewport = Object.assign(new EventTarget(), { height: 844, offsetTop: 0, scale: 1 });
    vi.stubGlobal("visualViewport", viewport);
    vi.stubGlobal("innerHeight", 844);
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === "(max-width: 1023px)", addEventListener() {}, removeEventListener() {},
    }));
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("iPhone");
    const input = createRef<HTMLTextAreaElement>();
    try {
      await act(async () => root.render(<QueryClientProvider client={client}>
        <MaxEditorLayout project={project} preview={null}>
          <PromptTextField inputRef={input} compact maxComposer aria-label="Сообщение" />
        </MaxEditorLayout>
      </QueryClientProvider>));
      const editor = host.querySelector<HTMLElement>(".max-editor")!;
      const frame = host.querySelector<HTMLIFrameElement>("iframe")!;
      await act(async () => frame.dispatchEvent(new Event("load")));
      Object.assign(viewport, { height: 400, offsetTop: 24 });
      await act(async () => input.current!.focus());
      await vi.waitFor(() => expect(document.body.style.getPropertyValue("--max-editor-visual-height")).toBe("400px"));
      expect(document.body.style.getPropertyValue("--max-editor-visual-top")).toBe("24px");
      const resetScroll = vi.spyOn(window, "scrollTo").mockImplementation(() => { vi.stubGlobal("scrollY", 0); });
      vi.stubGlobal("scrollY", 37);
      await act(async () => input.current!.blur());
      await vi.waitFor(() => expect(resetScroll).toHaveBeenCalledWith(0, 0));
      // The keyboard finishes sliding away after blur without another DOM event.
      Object.assign(viewport, { height: 844, offsetTop: 0 });
      await vi.waitFor(() => expect(document.body.style.getPropertyValue("--max-editor-visual-height")).toBe("844px"));
      expect(editor.hasAttribute("data-keyboard-open")).toBe(false);
    } finally { vi.unstubAllGlobals(); }
  });

  it("keeps the three mobile tabs inside preview and returns to the same chat draft", async () => {
    window.history.replaceState(null, "", "/max/qa");
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === "(max-width: 600px)", addEventListener() {}, removeEventListener() {},
    }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    try {
      await render("editor", project.name, true);
      const draft = host.querySelector<HTMLInputElement>('[aria-label="Черновик"]')!;
      await act(async () => host.querySelector<HTMLButtonElement>('.max-mobile-nav [data-max-tab="preview"]')!.click());
      const tabs = document.querySelector('[role="dialog"] nav[aria-label="Основные разделы"]');
      expect(tabs).not.toBeNull();
      expect([...tabs!.children].map(control => control.textContent)).toEqual(["Чат", "Превью", "Управление"]);
      expect(tabs!.querySelector('[aria-current="page"]')?.textContent).toBe("Превью");
      await act(async () => tabs!.querySelector<HTMLAnchorElement>('a[href="/max/qa/manage"]')!.click());
      expect(confirm).toHaveBeenCalledOnce();
      expect(tabs!.isConnected).toBe(true);
      await act(async () => tabs!.querySelector<HTMLButtonElement>('[data-max-tab="chat"]')!.click());
      expect(document.querySelector('[role="dialog"]')).toBeNull();
      expect(draft.isConnected && draft.value === "Не потерять").toBe(true);
    } finally { vi.unstubAllGlobals(); }
  });

  it("guards a mobile bottom-tab departure when the chat has an unsent draft", async () => {
    window.history.replaceState(null, "", "/max/qa");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await render("editor", project.name, true);
    await act(async () => host.querySelector<HTMLAnchorElement>('.max-mobile-nav a[href="/max/qa/manage"]')!.click());
    expect(confirm).toHaveBeenCalledOnce();
    expect(host.querySelector<HTMLInputElement>('[aria-label="Черновик"]')?.value).toBe("Не потерять");
  });

  it("follows a real project link and closes navigation", async () => {
    await render("section");
    await open();
    await act(async () => { await vi.waitFor(() => expect(document.querySelector('a[href="/max/other"]')).not.toBeNull()); });
    const link = document.querySelector<HTMLAnchorElement>('a[href="/max/other"]')!;
    link.addEventListener("click", event => event.preventDefault(), { once: true });
    await act(async () => link.click());
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
  });

  it.each(["editor", "section"] as const)("preserves the menu and draft when leaving %s is cancelled", async surface => {
    window.history.replaceState(null, "", "/max/qa" + (surface === "section" ? "/manage" : ""));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await render(surface, project.name, true);
    await open();
    await act(async () => { await vi.waitFor(() => expect(document.querySelector('a[href="/max/other"]')).not.toBeNull()); });
    await act(async () => document.querySelector<HTMLAnchorElement>('a[href="/max/other"]')!.click());
    expect(confirm).toHaveBeenCalledOnce();
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(trigger().getAttribute("aria-expanded")).toBe("true");
    await act(async () => document.querySelector<HTMLButtonElement>('[data-testid="max-account-menu-trigger"]')!.click());
    await act(async () => document.querySelector<HTMLAnchorElement>('[role="dialog"] a[href="/billing"]')!.click());
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(document.querySelector('[data-testid="max-account-menu"]')).not.toBeNull();
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it("keeps long names available without changing identity structure", async () => {
    const name = "Очень длинное имя приложения ".repeat(8);
    for (const surface of ["editor", "section"] as const) {
      await render(surface, name);
      expect(trigger().title).toBe(name);
      expect(trigger().querySelector("strong")?.textContent).toBe(name);
      expect(trigger().querySelector("small")?.textContent).toBe("Yleum");
      expect(trigger().querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    }
  });

  it("recovers a failed project list while keeping account navigation available", async () => {
    boundary.projects.mockRejectedValueOnce(new Error("network unavailable"));
    boundary.profile.mockRejectedValueOnce(new Error("profile unavailable"));
    await render("section");
    await open();
    await act(async () => { await vi.waitFor(() => expect(document.body.textContent).toContain("Не удалось загрузить список проектов.")); });
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.querySelector('[data-testid="max-account-menu-trigger"]')?.textContent).toContain("Аккаунт");
    const retry = [...dialog.querySelectorAll("button")].find(button => button.textContent === "Повторить")!;
    await act(async () => retry.click());
    await act(async () => { await vi.waitFor(() => expect(dialog.querySelector('a[href="/max/other"]')).not.toBeNull()); });
    expect(boundary.projects).toHaveBeenCalledTimes(2);
  });

  it("shows only freshly confirmed account identity when a shared profile cache exists", async () => {
    client.setQueryData(OWNER_PROFILE_QUERY_KEY, { id: "old-owner", email: "previous@example.test" });
    let resolveProfile!: (profile: { id: string; email: string }) => void;
    boundary.profile.mockImplementationOnce(() => new Promise(resolve => { resolveProfile = resolve; }));
    await render("section");
    await open();
    expect(document.querySelector('[role="dialog"]')?.textContent).not.toContain("previous@example.test");
    expect(document.querySelector('[data-testid="max-account-menu-trigger"]')?.textContent).toContain("Аккаунт");
    await act(async () => resolveProfile({ id: "current-owner", email: "current@example.test" }));
    await act(async () => { await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')?.textContent).toContain("current@example.test")); });
    expect(document.querySelector('[role="dialog"]')?.textContent).not.toContain("previous@example.test");
  });

  it("never falls back to an old account email when the fresh open request fails", async () => {
    client.setQueryData(OWNER_PROFILE_QUERY_KEY, { id: "old-owner", email: "previous@example.test" });
    boundary.profile.mockRejectedValueOnce(new Error("identity unavailable"));
    await render("section");
    await open();
    await act(async () => { await vi.waitFor(() => expect(client.isFetching()).toBe(0)); });
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).not.toContain("previous@example.test");
    expect(dialog.querySelector('[data-testid="max-account-menu-trigger"]')?.textContent).toContain("Аккаунт");
    await act(async () => dialog.querySelector<HTMLButtonElement>('[data-testid="max-account-menu-trigger"]')!.click());
    expect(dialog.querySelector('a[href="/account/security"]')).not.toBeNull();
    expect(dialog.querySelector('button[type="submit"]')?.textContent).toContain("Выйти");
  });

  it("withholds cached project names until the current open fetch succeeds", async () => {
    client.setQueryData(["projects"], [{ id: "old-project", name: "Предыдущий аккаунт", template: "max_miniapp" }]);
    let resolveProjects!: (projects: typeof applications) => void;
    boundary.projects.mockImplementationOnce(() => new Promise(resolve => { resolveProjects = resolve; }));
    await render("section");
    await open();
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).not.toContain("Предыдущий аккаунт");
    expect(dialog.querySelector('a[href="/max/old-project"]')).toBeNull();
    expect(dialog.textContent).toContain("Загружаем проекты…");
    await act(async () => resolveProjects(applications));
    await act(async () => { await vi.waitFor(() => expect(dialog.querySelector('a[href="/max/other"]')).not.toBeNull()); });
    expect(dialog.querySelector('a[href="/max/old-project"]')).toBeNull();
  });

  it("keeps stale projects hidden on fetch error and refreshes them through retry", async () => {
    client.setQueryData(["projects"], [{ id: "old-project", name: "Предыдущий аккаунт", template: "max_miniapp" }]);
    boundary.projects.mockRejectedValueOnce(new Error("project list unavailable"));
    await render("section");
    await open();
    await act(async () => { await vi.waitFor(() => expect(document.body.textContent).toContain("Не удалось загрузить список проектов.")); });
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).not.toContain("Предыдущий аккаунт");
    expect(dialog.querySelector('a[href="/max/old-project"]')).toBeNull();
    await act(async () => [...dialog.querySelectorAll("button")].find(button => button.textContent === "Повторить")!.click());
    await act(async () => { await vi.waitFor(() => expect(dialog.querySelector('a[href="/max/other"]')).not.toBeNull()); });
    expect(dialog.querySelector('a[href="/max/old-project"]')).toBeNull();
  });

  it("does not join an earlier session's already-running shared owner-profile request", async () => {
    let resolveOld!: (profile: { id: string; email: string }) => void;
    let resolveCurrent!: (profile: { id: string; email: string }) => void;
    boundary.profile
      .mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveCurrent = resolve; }));
    const oldRead = client.fetchQuery({ queryKey: OWNER_PROFILE_QUERY_KEY, queryFn: boundary.profile });
    await vi.waitFor(() => expect(boundary.profile).toHaveBeenCalledOnce());
    await render("section");
    await open();
    expect(boundary.profile).toHaveBeenCalledTimes(2);
    await act(async () => {
      resolveOld({ id: "old-owner", email: "previous@example.test" });
      await oldRead;
    });
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).not.toContain("previous@example.test");
    expect(dialog.querySelector('[data-testid="max-account-menu-trigger"]')?.textContent).toContain("Аккаунт");
    await act(async () => resolveCurrent({ id: "current-owner", email: "current@example.test" }));
    await act(async () => { await vi.waitFor(() => expect(dialog.textContent).toContain("current@example.test")); });
    expect(dialog.textContent).not.toContain("previous@example.test");
  });

  it("does not join an earlier session's already-running shared project-list request", async () => {
    let resolveOld!: (projects: typeof applications) => void;
    let resolveCurrent!: (projects: typeof applications) => void;
    boundary.projects
      .mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveCurrent = resolve; }));
    const oldRead = client.fetchQuery({ queryKey: ["projects"], queryFn: boundary.projects });
    await vi.waitFor(() => expect(boundary.projects).toHaveBeenCalledOnce());
    await render("section");
    await open();
    expect(boundary.projects).toHaveBeenCalledTimes(2);
    await act(async () => {
      resolveOld([{ id: "old-project", name: "Предыдущий аккаунт", template: "max_miniapp" }]);
      await oldRead;
    });
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).not.toContain("Предыдущий аккаунт");
    expect(dialog.querySelector('a[href="/max/old-project"]')).toBeNull();
    expect(dialog.textContent).toContain("Загружаем проекты…");
    await act(async () => resolveCurrent(applications));
    await act(async () => { await vi.waitFor(() => expect(dialog.querySelector('a[href="/max/other"]')).not.toBeNull()); });
    expect(dialog.querySelector('a[href="/max/old-project"]')).toBeNull();
  });

  it("starts new owned reads on reopen while the previous opening is still pending", async () => {
    let resolveFirstProfile!: (profile: { id: string; email: string }) => void;
    let resolveNextProfile!: (profile: { id: string; email: string }) => void;
    let resolveFirstProjects!: (projects: typeof applications) => void;
    let resolveNextProjects!: (projects: typeof applications) => void;
    boundary.profile
      .mockImplementationOnce(() => new Promise(resolve => { resolveFirstProfile = resolve; }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveNextProfile = resolve; }));
    boundary.projects
      .mockImplementationOnce(() => new Promise(resolve => { resolveFirstProjects = resolve; }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveNextProjects = resolve; }));
    await render("section");
    await open();
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Закрыть окно"]')!.click());
    await open();
    expect(boundary.profile).toHaveBeenCalledTimes(2);
    expect(boundary.projects).toHaveBeenCalledTimes(2);
    await act(async () => {
      resolveFirstProfile({ id: "old-owner", email: "previous@example.test" });
      resolveFirstProjects([{ id: "old-project", name: "Предыдущий аккаунт", template: "max_miniapp" }]);
    });
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).not.toContain("previous@example.test");
    expect(dialog.textContent).not.toContain("Предыдущий аккаунт");
    expect(dialog.textContent).toContain("Загружаем проекты…");
    await act(async () => {
      resolveNextProfile({ id: "current-owner", email: "current@example.test" });
      resolveNextProjects(applications);
    });
    await act(async () => { await vi.waitFor(() => expect(dialog.textContent).toContain("current@example.test")); });
    expect(dialog.querySelector('a[href="/max/other"]')).not.toBeNull();
    expect(dialog.querySelector('a[href="/max/old-project"]')).toBeNull();
  });
});
