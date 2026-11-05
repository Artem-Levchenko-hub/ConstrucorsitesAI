import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MaxOwnerCatalog } from "@/components/max/MaxOwnerCatalog";
import { getMaxProjectContent, saveMaxProjectConfig } from "@/lib/api/max-studio";
import { rebaseCatalogDraft } from "@/lib/max-catalog";
import type { MaxContentItem, MaxProjectContent } from "@/lib/api/types";

vi.mock("@/components/max/MaxSectionShell", () => ({ MaxSectionShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/owner-profile", () => ({ getOwnerProfile: async () => ({ id: "owner", user_chat_messages_limit: 1, user_chat_messages_remaining: 0 }) }));

const item: MaxContentItem = { id: "historical-id", title: "Консультация", category: "Услуги", description: "Подробное описание", price: "По запросу", availability: "on_request", options: ["30 минут"], image_url: "", action_label: "Записаться", active: true };
const status = { state: "applied" as const, desired_config_version: 7, confirmed_config_version: 7, controller_config_version: 7, checked_at: null, reason: null };
const versionConflict = { code: "conflict", message: "Версия устарела", details: { reason: "config_version_conflict" } };
let data: MaxProjectContent;
let requests: Array<{ url: string; init: RequestInit }>;
let root: Root;
let client: QueryClient;
let node: HTMLDivElement;
let patchError: { code: string; message: string; details?: Record<string, unknown> } | null;
let holdPatch: ((response: Response) => void) | null;
let pendingPatch: boolean;
let holdImage: ((response: Response) => void) | null;
let pendingGet: boolean;
let holdGet: ((response: Response) => void) | null;
let retryApplies: boolean;
const legacyConfig = { app_name: "Старый каталог", summary: "Прежнее описание", management_modules: ["orders"], legacy_orders_table: "old_orders" };
let sourceVersion: number | null;
let sourcePutFailure: Response | Error | null;
let sourceConfig: typeof legacyConfig;
let loseSourceResponse: boolean;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  data = { project_id: "owned", config_version: 7, content: [{ ...item }], saved: { state: "saved", config_version: 7 }, public: { ...status }, preview: { ...status } };
  requests = []; patchError = null; pendingPatch = false; holdPatch = null; holdImage = null; pendingGet = false; holdGet = null; retryApplies = false;
  sourceVersion = null;
  sourcePutFailure = null;
  sourceConfig = legacyConfig;
  loseSourceResponse = false;
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    requests.push({ url, init });
    if (url.endsWith("/content-image")) return new Promise<Response>(resolve => { holdImage = resolve; });
    if (url.endsWith("/max/config")) {
      if (init.method === "PUT" && sourceVersion !== null) {
        if (sourcePutFailure instanceof Error) throw sourcePutFailure;
        if (sourcePutFailure) return sourcePutFailure;
        const config = JSON.parse(init.body as string);
        config.content = config.content.map((item: MaxContentItem) => ({ ...item, title: item.title.trim() }));
        const changed = JSON.stringify(config) !== JSON.stringify({ ...sourceConfig, content: data.content });
        if (changed && new Headers(init.headers).get("X-Max-Config-Version") !== String(sourceVersion)) {
          return Response.json({ error: versionConflict }, { status: 409 });
        }
        const version = changed ? ++sourceVersion : sourceVersion;
        data = { ...data, content: config.content, config_version: version, saved: { state: "saved", config_version: version } };
        if (loseSourceResponse) { loseSourceResponse = false; throw new TypeError("Lost PUT response"); }
        return Response.json({ project_id: "owned", config_version: version, config, application_mode: "source" });
      }
      return Response.json(sourceVersion === null
        ? { project_id: "owned", config_version: data.config_version, config: {}, application_mode: "runtime" }
        : { project_id: "owned", config_version: sourceVersion, config: { ...sourceConfig, content: data.content }, application_mode: "source" });
    }
    if (url === "/api/auth/me") return Response.json({ id: "owner", user_chat_messages_limit: 1, user_chat_messages_remaining: 0 });
    if (!init.method && pendingGet) return new Promise<Response>(resolve => { holdGet = resolve; });
    if (init.method === "PATCH") {
      if (patchError) return Response.json({ error: patchError }, { status: 409 });
      if (pendingPatch) return new Promise<Response>(resolve => { holdPatch = resolve; });
      const body = JSON.parse(init.body as string);
      const changed = JSON.stringify(body.content) !== JSON.stringify(data.content);
      const version = data.config_version + (changed ? 1 : 0);
      data = { ...data, content: body.content, config_version: version, saved: { state: "saved", config_version: version } };
      if (!changed && retryApplies) {
        const applied = { ...status, desired_config_version: version, confirmed_config_version: version, controller_config_version: version };
        data = { ...data, public: applied, preview: applied };
      }
    }
    return Response.json(data);
  }));
});
afterEach(async () => { await act(async () => root.unmount()); node.remove(); client.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function mount() { await act(async () => root.render(<QueryClientProvider client={client}><MaxOwnerCatalog projectId="owned" projectName="Проект" /></QueryClientProvider>)); await settle(); }
function button(label: string) { return [...document.querySelectorAll<HTMLButtonElement>("button")].find(element => element.textContent?.trim() === label)!; }
async function click(label: string) { await act(async () => button(label).click()); await settle(); }
async function input(label: string, value: string) {
  await act(async () => {
    const field = document.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function patches() { return requests.filter(request => request.init.method === "PATCH"); }

const sourceDependentQueries = [
  ["snapshots", "owned"], ["max-preview-session", "owned"], ["max-readiness", "owned"],
  ["project-versions", "owned", "current-build"],
];
function warmQuery(key: string[]) {
  client.setQueryDefaults(key, { gcTime: Infinity, staleTime: 60_000 });
  client.setQueryData(key, "previous HEAD");
}

it("saves a source catalog through the versioned config PUT without replacing the rest of the config", async () => {
  sourceVersion = 7;
  await mount(); await click("Скрыть"); await click("Сохранить изменения");
  const writes = requests.filter(request => request.init.method);
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ url: "/api/projects/owned/max/config", init: { method: "PUT" } });
  expect(new Headers(writes[0].init.headers).get("X-Max-Config-Version")).toBe("7");
  expect(JSON.parse(writes[0].init.body as string)).toEqual({ ...legacyConfig, content: [{ ...item, active: false }] });
  expect(node.textContent).toContain("Позиции сохранены в исходниках");
  expect(node.textContent).not.toContain("Правки ещё не сохранены");
});

it("invalidates the source HEAD caches immediately after PUT, while delivery readback is pending", async () => {
  sourceVersion = 7;
  for (const key of [...sourceDependentQueries, ["snapshots", "another-project"]]) warmQuery(key);
  await mount(); await click("Скрыть");
  pendingGet = true;
  await click("Сохранить изменения");
  expect(sourceDependentQueries.map(key => client.getQueryState(key)?.isInvalidated)).toEqual([true, true, true, true]);
  expect(client.getQueryState(["snapshots", "another-project"])?.isInvalidated).toBe(false);
  expect(holdGet).toBeTypeOf("function");
  pendingGet = false;
  await act(async () => holdGet!(Response.json(data)));
});

it("keeps the source catalog draft when the full configuration baseline has moved", async () => {
  sourceVersion = 8;
  await mount(); await click("Скрыть"); await click("Сохранить изменения");
  const writes = requests.filter(request => request.init.method);
  expect(writes.map(request => request.init.method)).toEqual(["PUT"]);
  expect(new Headers(writes[0].init.headers).get("X-Max-Config-Version")).toBe("7");
  expect(data.content).toEqual([item]);
  expect(node.textContent).toContain("Правки ещё не сохранены");
  expect(node.querySelector('[role="alert"]')).not.toBeNull();
});

it("recovers a lost source PUT response without overwriting a newer profile or advancing its version", async () => {
  sourceVersion = 7;
  loseSourceResponse = true;
  await mount(); await click("Изменить");
  await input("Название элемента 1", "  Обновлённая консультация  ");
  await click("Сохранить позицию");
  expect(data.config_version).toBe(8);
  expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  sourceConfig = { ...sourceConfig, summary: "Новое описание из другой вкладки" };
  sourceVersion = 9;
  await click("Сохранить позицию");
  const writes = requests.filter(request => request.init.method);
  expect(writes.map(request => request.init.method)).toEqual(["PUT", "PUT"]);
  expect(new Headers(writes[1].init.headers).get("X-Max-Config-Version")).toBe("7");
  expect(JSON.parse(writes[1].init.body as string).summary).toBe(sourceConfig.summary);
  expect(data.config_version).toBe(9);
  expect(data.content[0].title).toBe("Обновлённая консультация");
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});

it("saves catalog data for a source project without a snapshot using the same expected version", async () => {
  sourceVersion = 7;
  sourcePutFailure = Response.json({ error: { code: "project_empty", message: "У проекта нет исходного снимка" } }, { status: 409 });
  for (const key of sourceDependentQueries) warmQuery(key);
  await mount(); await click("Скрыть"); await click("Сохранить изменения");
  expect(requests.flatMap(request => request.init.method ? [request.init.method] : [])).toEqual(["PUT", "PATCH"]);
  expect(JSON.parse(patches()[0].init.body as string)).toEqual({ expected_config_version: 7, content: [{ ...item, active: false }] });
  expect(sourceDependentQueries.map(key => client.getQueryState(key)?.isInvalidated)).toEqual([false, false, false, false]);
  expect(node.textContent).not.toContain("Правки ещё не сохранены");
});

it.each([
  ["version conflict", () => Response.json({ error: versionConflict }, { status: 409 })],
  ["dangling snapshot", () => Response.json({ error: { code: "project_empty", message: "Текущий снимок проекта не найден" } }, { status: 409 })],
  ["network error", () => new TypeError("Failed to fetch")],
] as const)("keeps the draft after a source PUT %s without issuing a second write", async (_case, failure) => {
  sourceVersion = 7;
  sourcePutFailure = failure();
  await mount(); await click("Скрыть"); await click("Сохранить изменения");
  expect(requests.flatMap(request => request.init.method ? [request.init.method] : [])).toEqual(["PUT"]);
  expect(node.textContent).toContain("Правки ещё не сохранены");
  expect(node.querySelector('[role="alert"]')).not.toBeNull();
});

it("creates and edits through content-only saves, preserving item IDs and free-text prices", async () => {
  await mount(); await click("Добавить позицию");
  await input("Название элемента 1", "Массаж"); await input("Цена или подпись элемента 1", "от 2 000 ₽");
  await click("Сохранить позицию");
  const created = JSON.parse(patches()[0].init.body as string);
  expect(created.expected_config_version).toBe(7);
  expect(created.content[0]).toEqual(item);
  expect(created.content[1]).toMatchObject({ title: "Массаж", price: "от 2 000 ₽", active: true });
  expect(created.content[1].id).not.toBe(item.id);
  await act(async () => [...node.querySelectorAll<HTMLButtonElement>("button")].find(element => element.textContent === "Изменить")!.click());
  await input("Название элемента 1", "Новая консультация"); await click("Сохранить позицию");
  const edited = JSON.parse(patches()[1].init.body as string);
  expect(edited.content[0]).toEqual({ ...item, title: "Новая консультация" });
  expect(requests.filter(request => request.init.method).map(request => request.url)).toEqual(Array(2).fill("/api/projects/owned/max/content"));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});

it("hides and restores without removing a position, with search and visibility filtering", async () => {
  await mount(); await click("Скрыть"); await click("Сохранить изменения");
  expect(data.content).toEqual([{ ...item, active: false }]);
  const filter = node.querySelector<HTMLSelectElement>('select[aria-label="Видимость позиций"]')!;
  await act(async () => { filter.value = "visible"; filter.dispatchEvent(new Event("change", { bubbles: true })); });
  expect(node.textContent).toContain("Ничего не найдено");
  await act(async () => { filter.value = "hidden"; filter.dispatchEvent(new Event("change", { bubbles: true })); });
  expect(node.textContent).toContain(item.title);
  await input("Поиск по каталогу", "другой товар"); expect(node.textContent).toContain("Ничего не найдено");
  await input("Поиск по каталогу", "Консультация"); await click("Показать"); await click("Сохранить изменения");
  expect(data.content).toEqual([item]);
});

it("can cancel a new unsaved item without discarding other pending edits", async () => {
  await mount(); await click("Скрыть"); await click("Добавить позицию");
  await input("Название элемента 1", "Не добавлять"); await click("Отменить добавление");
  expect(node.textContent).not.toContain("Не добавлять"); expect(node.textContent).toContain("Правки ещё не сохранены");
  expect(patches()).toHaveLength(0); await click("Сохранить изменения");
  expect(data.content).toEqual([{ ...item, active: false }]);
});

it("clears a category filter when the last item leaves that category", async () => {
  await mount();
  const category = node.querySelector<HTMLSelectElement>('select[aria-label="Раздел каталога"]')!;
  await act(async () => { category.value = "Услуги"; category.dispatchEvent(new Event("change", { bubbles: true })); });
  await click("Изменить"); await input("Раздел элемента 1", ""); await click("Сохранить позицию");
  expect(node.querySelector('select[aria-label="Раздел каталога"]')).toBeNull();
  expect(node.querySelector(".max-catalog-card h2")?.textContent).toBe(item.title);
  expect(node.textContent).not.toContain("Ничего не найдено");
});

it("pauses background reads during PATCH and rejects a queued older GET after the saved response", async () => {
  await mount(); await click("Скрыть"); pendingPatch = true; await click("Сохранить изменения");
  const previous = structuredClone(data);
  const readsBefore = requests.filter(request => !request.init.method).length;
  pendingGet = true;
  // Active query invalidation models a focus/poll read during the mutation.
  const background = client.refetchQueries({ queryKey: ["max-content", "owned"], type: "active" });
  await settle();
  const backgroundReads = requests.filter(request => !request.init.method).length - readsBefore;
  // A queued explicit read can still start after onMutate's first cancellation.
  const queued = client.fetchQuery({ queryKey: ["max-content", "owned"], queryFn: () => getMaxProjectContent("owned") }).catch(() => null);
  await settle();
  const resolveStale = holdGet!;
  expect(resolveStale).toBeTypeOf("function");
  data = { ...data, config_version: 8, content: [{ ...item, active: false }], saved: { state: "saved", config_version: 8 }, public: { ...status, state: "pending", desired_config_version: 8 } };
  pendingGet = false;
  await act(async () => holdPatch!(Response.json(data))); await settle();
  await act(async () => { resolveStale(Response.json(previous)); await queued; await background; }); await settle();
  expect(client.getQueryData<MaxProjectContent>(["max-content", "owned"])?.config_version).toBe(8);
  expect(node.querySelector<HTMLButtonElement>('button[aria-label="Показать Консультация"]')).not.toBeNull();
  expect(node.textContent).toContain("Обновляется");
  expect(backgroundReads).toBe(0);
});

it("does not advance a dirty baseline on a background GET or lose a draft after 409", async () => {
  await mount(); await click("Изменить");
  data = { ...data, config_version: 8, content: [{ ...item, title: "Название из другой вкладки" }] };
  await act(async () => { client.setQueryData(["max-content", "owned"], data); });
  expect(document.querySelector<HTMLInputElement>('input[aria-label="Название элемента 1"]')!.value).toBe(item.title);
  await input("Цена или подпись элемента 1", "3 000 ₽");
  await act(async () => { client.setQueryData(["max-content", "owned"], { ...data }); });
  patchError = versionConflict; await click("Сохранить позицию");
  expect(JSON.parse(patches()[0].init.body as string).expected_config_version).toBe(7);
  expect(document.querySelector<HTMLInputElement>('input[aria-label="Цена или подпись элемента 1"]')!.value).toBe("3 000 ₽");
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Каталог изменился. Черновик сохранён в форме.");
  await click("Сохранить позицию");
  expect(JSON.parse(patches()[1].init.body as string).expected_config_version).toBe(7);
  await click("К списку"); await click("Перенести мои правки");
  expect(node.textContent).toContain("Название из другой вкладки");
  expect(node.textContent).toContain("3 000 ₽");
  expect(patches()).toHaveLength(2);
  patchError = null; await click("Сохранить изменения");
  expect(JSON.parse(patches()[2].init.body as string)).toMatchObject({ expected_config_version: 8, content: [{ ...item, title: "Название из другой вкладки", price: "3 000 ₽" }] });
});

it.each([
  { code: "generation_active", message: "Дождитесь завершения сборки и сохраните каталог ещё раз" },
  { code: "operation_conflict", message: "Дождитесь завершения публикации", details: { reason: "publication_active" } },
])("retains a new item and retries the same form after $code without a catalog rebase", async error => {
  await mount(); await click("Добавить позицию");
  await input("Название элемента 1", "Новая услуга");
  await input("Цена или подпись элемента 1", "3 000 ₽");
  patchError = error; await click("Сохранить позицию");
  const first = JSON.parse(patches()[0].init.body as string);
  expect(first.expected_config_version).toBe(7);
  expect(data.content).toEqual([item]);
  const dialog = document.querySelector('[role="dialog"]')!;
  expect(dialog.textContent).toContain(error.message);
  expect(dialog.textContent).toContain("Черновик сохранён в форме");
  expect(dialog.textContent).not.toContain("Каталог изменился");
  expect(button("Перенести мои правки")).toBeUndefined();
  expect(button("Сохранить позицию").disabled).toBe(false);
  expect(document.querySelector<HTMLInputElement>('input[aria-label="Название элемента 1"]')!.value).toBe("Новая услуга");
  expect(document.querySelector<HTMLInputElement>('input[aria-label="Цена или подпись элемента 1"]')!.value).toBe("3 000 ₽");
  patchError = null; await click("Сохранить позицию");
  expect(JSON.parse(patches()[1].init.body as string)).toEqual(first);
  expect(data.config_version).toBe(8);
  expect(data.content).toHaveLength(2);
  expect(data.content[1]).toMatchObject({ title: "Новая услуга", price: "3 000 ₽" });
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(node.textContent).not.toContain("Есть несохранённые правки");
});

it("shows an unrelated 409 honestly without claiming that the catalog changed", async () => {
  await mount(); await click("Изменить"); await input("Название элемента 1", "Мой черновик");
  patchError = { code: "project_empty", message: "У проекта нет исходного снимка" };
  await click("Сохранить позицию");
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain(patchError.message);
  expect(document.body.textContent).not.toContain("Каталог изменился");
  expect(button("Перенести мои правки")).toBeUndefined();
  expect(document.querySelector<HTMLInputElement>('input[aria-label="Название элемента 1"]')!.value).toBe("Мой черновик");
});

it("shows pending save, prevents duplicate submission, then restores honest delivery states after remount", async () => {
  await mount(); await click("Скрыть"); pendingPatch = true; await click("Сохранить изменения");
  expect(button("Сохранить изменения").disabled).toBe(true);
  expect(patches()).toHaveLength(1);
  data = { ...data, config_version: 8, content: [{ ...item, active: false }], saved: { state: "saved", config_version: 8 }, public: { ...status, state: "pending", desired_config_version: 8 }, preview: { ...status, state: "deferred", desired_config_version: 8 } };
  await act(async () => holdPatch!(Response.json(data))); await settle();
  expect(node.textContent).toContain("Сохранены"); expect(node.textContent).toContain("Обновляется"); expect(node.textContent).toContain("Обновится после пробуждения");
  await act(async () => root.render(null)); client.clear(); await mount();
  expect(node.textContent).toContain("Обновляется"); expect(node.textContent).not.toContain("Есть несохранённые правки");
  expect(data.content[0].active).toBe(false); expect(patches()).toHaveLength(1);
});

it("explicitly reapplies already saved pending content without changing its content or version", async () => {
  await mount(); await click("Скрыть"); pendingPatch = true; await click("Сохранить изменения");
  data = { ...data, config_version: 8, content: [{ ...item, active: false }], saved: { state: "saved", config_version: 8 }, public: { ...status, state: "pending", desired_config_version: 8 }, preview: { ...status, state: "unknown", desired_config_version: 8 } };
  await act(async () => holdPatch!(Response.json(data))); await settle();
  expect(node.textContent).toContain("Сохранены");
  expect(node.textContent).not.toContain("Есть несохранённые правки");
  const saved = structuredClone(data);
  pendingPatch = false; retryApplies = true;
  await click("Повторить применение");
  expect(JSON.parse(patches()[1].init.body as string)).toEqual({ expected_config_version: 8, content: saved.content });
  expect(data.config_version).toBe(8); expect(data.content).toEqual(saved.content);
  expect(node.textContent).toContain("Сохранены"); expect(node.textContent).not.toContain("Обновляется");
  expect([...node.querySelectorAll('.max-catalog-delivery[data-state="applied"]')]).toHaveLength(2);
  expect(button("Повторить применение")).toBeUndefined();
  expect(requests.filter(request => request.init.method).map(request => request.url)).toEqual(Array(2).fill("/api/projects/owned/max/content"));
});

it("keeps saved data and local drafts safe when an explicit reapply meets a newer catalog", async () => {
  data.public = { ...status, state: "unknown" };
  await mount();
  data = { ...data, config_version: 8, content: [{ ...item, title: "Изменено в другой вкладке" }], saved: { state: "saved", config_version: 8 } };
  patchError = versionConflict; await click("Повторить применение");
  expect(JSON.parse(patches()[0].init.body as string)).toEqual({ expected_config_version: 7, content: [item] });
  expect(data.content[0].title).toBe("Изменено в другой вкладке");
  expect(data.config_version).toBe(8);
  expect(node.textContent).toContain("Сохранены"); expect(node.textContent).not.toContain("Не удалось сохранить");
  expect(node.textContent).toContain("Обновите каталог перед повторным применением");
  await click("Изменить"); await input("Цена или подпись элемента 1", "Мой черновик цены");
  expect(button("Повторить применение").disabled).toBe(true);
  await click("Повторить применение");
  expect(patches()).toHaveLength(1);
  expect(document.querySelector<HTMLInputElement>('input[aria-label="Цена или подпись элемента 1"]')!.value).toBe("Мой черновик цены");
  expect(data.content[0].price).toBe(item.price);
});

it("never opens generation automatically for a legacy integration or an ordinary save", async () => {
  data.public = { ...status, state: "blocked", reason: "catalog_integration_required" };
  await mount(); expect(button("Подключить каталог")).toBeDefined();
  await click("Скрыть"); expect(button("Подключить каталог").disabled).toBe(true); await click("Сохранить изменения");
  expect(requests.filter(request => request.init.method).map(request => request.url)).toEqual(["/api/projects/owned/max/content"]);
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  await click("Подключить каталог");
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Подключить каталог к приложению");
  expect(button("Запустить доработку").disabled).toBe(true);
  expect(requests.filter(request => request.init.method === "POST")).toHaveLength(0);
});

it("offers explicit catalog connection when only the unpublished preview needs it", async () => {
  data.public = { ...status, state: "not_published" };
  data.preview = { ...status, state: "unknown", reason: "catalog_integration_required" };
  await mount();
  expect(button("Подключить каталог")).toBeDefined();
  expect(requests.filter(request => request.init.method)).toHaveLength(0);
});

it.each(["", "https://images.example/current.png"])("offers file upload and a URL from the card photo without opening a picker or saving (%s)", async imageUrl => {
  data.content = [{ ...item, image_url: imageUrl }];
  const filePicker = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => {});
  await mount();
  const photo = node.querySelector<HTMLButtonElement>(`button[aria-label="${imageUrl ? "Изменить" : "Добавить"} фото: ${item.title}"]`);
  expect(photo).toBeInstanceOf(HTMLButtonElement);
  await act(async () => photo!.click()); await settle();
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Фото позиции");
  expect(button(imageUrl ? "Заменить фото" : "Загрузить фото")).toBeDefined();
  expect(document.querySelector<HTMLInputElement>('input[aria-label="Ссылка на фото элемента 1"]')?.value).toBe(imageUrl);
  expect(filePicker).not.toHaveBeenCalled();
  await click("К списку");
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(node.textContent).not.toContain("Правки ещё не сохранены");
  expect(requests.filter(request => request.init.method)).toHaveLength(0);
  expect(data.content).toEqual([{ ...item, image_url: imageUrl }]);
});

it("keeps photo URL edits in the ordinary draft until an explicit content save", async () => {
  await mount();
  await act(async () => node.querySelector<HTMLButtonElement>(`button[aria-label="Добавить фото: ${item.title}"]`)!.click()); await settle();
  await input("Ссылка на фото элемента 1", "https://images.example/linked.png");
  expect(requests.filter(request => request.init.method)).toHaveLength(0);
  await click("Все поля позиции");
  expect(document.querySelector<HTMLInputElement>('input[aria-label="Ссылка на фото элемента 1"]')!.value).toBe("https://images.example/linked.png");
  await click("Сохранить позицию");
  expect(JSON.parse(patches()[0].init.body as string)).toEqual({ expected_config_version: 7, content: [{ ...item, image_url: "https://images.example/linked.png" }] });
  expect(requests.filter(request => request.init.method).map(request => request.url)).toEqual(["/api/projects/owned/max/content"]);
});

it("discards photo edits with the existing cancel-draft action while preserving saved data", async () => {
  await mount(); await click("Скрыть");
  await act(async () => node.querySelector<HTMLButtonElement>(`button[aria-label="Добавить фото: ${item.title}"]`)!.click()); await settle();
  await input("Ссылка на фото элемента 1", "https://images.example/draft.png");
  await click("К списку");
  expect(node.querySelector<HTMLImageElement>(".max-catalog-photo")?.src).toBe("https://images.example/draft.png");
  expect(node.querySelector<HTMLButtonElement>(`button[aria-label="Показать ${item.title}"]`)).not.toBeNull();
  await click("Отменить правки");
  expect(node.querySelector("img.max-catalog-photo")).toBeNull();
  expect(node.querySelector<HTMLButtonElement>(`button[aria-label="Скрыть ${item.title}"]`)).not.toBeNull();
  expect(requests.filter(request => request.init.method)).toHaveLength(0);
  expect(data.content).toEqual([item]);
});

it("keeps the item form open during photo upload and saves its returned URL", async () => {
  await mount();
  await act(async () => node.querySelector<HTMLButtonElement>(`button[aria-label="Добавить фото: ${item.title}"]`)!.click()); await settle();
  await act(async () => {
    const field = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(field, "files", { configurable: true, value: [new File(["photo"], "photo.png", { type: "image/png" })] });
    field.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await settle(); expect(button("Сохранить позицию").disabled).toBe(true); expect(button("К списку").disabled).toBe(true);
  expect(button("Все поля позиции").disabled).toBe(true);
  await click("Закрыть");
  expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  await act(async () => holdImage!(Response.json({ url: "https://images.example/item.png" }))); await settle();
  expect(document.querySelector<HTMLInputElement>('input[aria-label="Ссылка на фото элемента 1"]')!.value).toBe("https://images.example/item.png");
  await click("Сохранить позицию");
  expect(JSON.parse(patches()[0].init.body as string).content[0]).toEqual({ ...item, image_url: "https://images.example/item.png" });
});

it("allows typing comma-separated variants without swallowing the separator", async () => {
  await mount(); await click("Изменить");
  await input("Варианты элемента 1", "S,");
  expect(document.querySelector<HTMLInputElement>('input[aria-label="Варианты элемента 1"]')!.value).toBe("S,");
  await input("Варианты элемента 1", "S, M"); await click("Сохранить позицию");
  expect(JSON.parse(patches()[0].init.body as string).content[0].options).toEqual(["S", "M"]);
});

it("rebases explicit edits without dropping concurrently added items or untouched fields", () => {
  const additional = { ...item, id: "server-addition", title: "Новинка" };
  const latest = { ...data, config_version: 9, content: [{ ...item, description: "Свежее описание" }, additional] };
  expect(rebaseCatalogDraft({ version: 7, base: [item], content: [{ ...item, active: false }] }, latest)).toEqual({ version: 9, base: latest.content, content: [{ ...item, description: "Свежее описание", active: false }, additional] });
});

it("sends the optional captured full-config baseline as X-Max-Config-Version", async () => {
  const config = {} as Parameters<typeof saveMaxProjectConfig>[1];
  await saveMaxProjectConfig("owned", config, 7);
  expect(requests[0].init.headers).toMatchObject({ "X-Max-Config-Version": "7" });
  await saveMaxProjectConfig("owned", config);
  expect(requests[1].init.headers).not.toHaveProperty("X-Max-Config-Version");
});
