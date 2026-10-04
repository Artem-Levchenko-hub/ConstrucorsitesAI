import { act, createElement } from "react";
import { createHash } from "node:crypto";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatMessage } from "@/components/workspace/ChatMessage";
import type { Message, IntegrationCatalog } from "@/lib/api/types";
import { builtinIntegrationRequests, recognizeBuiltinIntegrationRequest } from "@/lib/builtin-integration-prompts";
import { FigmaIntegrationHub } from "@/components/max/FigmaIntegrationHub";

const boundary = vi.hoisted(() => ({ send: vi.fn(), catalog: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/max-studio", () => ({ syncMaxManagedKit: async () => undefined }));
vi.mock("@/lib/api/messages", () => ({ sendPrompt: boundary.send }));
vi.mock("@/lib/api/app-integrations", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/api/app-integrations")>(), getIntegrationCatalog: boundary.catalog }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const originalAmoPrompt = "Добавь форму заявки с созданием лида в amoCRM. Имя и телефон обязательны; e-mail и комментарий необязательны. Показывай ошибки валидации у соответствующих полей, без тихого пропуска отправки. Для одного намерения пользователя сохраняй стабильный ключ идемпотентности, блокируй повторную отправку на время запроса и при неизвестном результате не создавай новый лид: покажи неизвестный исход и предложи сверку. Создавай лид только через доступный управляемый метод интеграции. При открытии, возобновлении и обновлении формы загружай собственную историю заявок через getYleumLeads() и текущий статус CRM через getYleumLeadStatus(id); показывай ошибки загрузки и время последнего обновления. Обрабатывай результат лида с details_status: recorded | unknown и необязательным warning: при unknown покажи предупреждение, а при сбое записи комментария никогда не повторяй исходное создание лида.\nИспользуй только доступные управляемые методы интеграции. Не запрашивай и не вставляй секреты в код или сообщения. Добавь состояния загрузки, пустого результата и ошибки. Проверь сценарий и сообщи, что проверено, а что требует проверки с реальным аккаунтом.";
let root: Root;
let container: HTMLDivElement;
let client: QueryClient | undefined;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks(); window.sessionStorage.clear();
  boundary.send.mockResolvedValue({ run_id: "fixture-run", run_status: "pending" });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); client?.clear(); client = undefined; container.remove(); });
function render(text: string, presentation: "default" | "studio" = "default") {
  const message: Message = {
    id: "fixture-message", project_id: "fixture-project", snapshot_id: null, role: "user", content: text,
    model_id: null, tokens_in: null, tokens_out: null, created_at: "2026-10-04T08:00:00Z",
  };
  act(() => root.render(createElement(ChatMessage, { message, presentation })));
  return message;
}

it.each(["default", "studio"] as const)("summarizes the exact built-in request in %s chat and reveals every original word", presentation => {
  const message = render(originalAmoPrompt, presentation);
  const heading = Array.from(container.querySelectorAll("h3")).find(el => el.textContent === "Добавить amoCRM");
  expect(heading).toBeDefined();
  expect(container.querySelectorAll("li")).toHaveLength(4);
  const button = container.querySelector<HTMLButtonElement>('button[aria-expanded]');
  expect(button?.textContent).toBe("Раскрыть полный запрос");
  expect(button?.getAttribute("aria-expanded")).toBe("false");
  expect(document.getElementById(button!.getAttribute("aria-controls")!)?.hidden).toBe(true);
  act(() => button!.focus());
  expect(document.activeElement).toBe(button);
  act(() => button!.click());
  expect(button?.getAttribute("aria-expanded")).toBe("true");
  const original = document.getElementById(button!.getAttribute("aria-controls")!);
  expect(original?.textContent).toBe(originalAmoPrompt);
  expect(original?.hidden).toBe(false);
  expect(message.content).toBe(originalAmoPrompt);
  act(() => button!.click());
  expect(button?.getAttribute("aria-expanded")).toBe("false");
  expect(document.getElementById(button!.getAttribute("aria-controls")!)?.hidden).toBe(true);
});

it.each([
  originalAmoPrompt + "\nПокажи форму в разделе продаж.",
  " " + originalAmoPrompt,
  originalAmoPrompt.replace("amoCRM", "Неизвестный CRM"),
])("preserves an edited or unknown request as ordinary chat prose", text => {
  render(text);
  expect(container.querySelector("h3")).toBeNull();
  expect(container.querySelector('button[aria-expanded]')).toBeNull();
  expect(container.textContent).toContain(text);
});

// Captured independently from the unchanged pre-refactor Hub payloads.
const originalPromptHashes: Record<string, string> = {
  "yookassa": "95266922bfff75aae13a8c9d904811169d900ea9db5dc3b2a5831bf89f5fa888",
  "iiko": "c16b8c296488dc3205ab1430c1b752a1ef9144f909fc752a0856e355c40d0fca",
  "bitrix24": "c775c82a1e7bc5e14aee9d14de7f4b303b70ea90816c022413fb471f9fdddd70",
  "amocrm": "2a16922b340ef92eb935dfde036c046032c755ea164854cfb67b43c52e41a1c5",
  "moysklad": "db038017e988370906b0530be67fcea4accf6691901538d38d8b36539194666a",
  "yandex_metrica": "cd04ac7a179ca67b366443de52edc166c617fdb3cf41bbeed7ea5bd89a8eadc9",
  "llmgw": "2e448fbe9d8f7aba452d7f833dbd602733644d938ef31e39501759ef072a8e55"
};

it.each(builtinIntegrationRequests)("keeps the full $provider generator request byte-for-byte", request => {
  expect(createHash("sha256").update(request.prompt).digest("hex")).toBe(originalPromptHashes[request.provider]);
  expect(recognizeBuiltinIntegrationRequest(request.prompt)).toBe(request);
  expect(request.outcomes.length).toBeGreaterThanOrEqual(2);
  expect(request.outcomes.length).toBeLessThanOrEqual(4);
  expect(recognizeBuiltinIntegrationRequest(request.prompt + " ")).toBeNull();
});

async function renderHub() {
  const data: IntegrationCatalog = {
    providers: [{ key: "amocrm", name: "amoCRM", category: "crm", description: "Заявки", capabilities: [], fields: [],
      available: true, recommended: false, requirement: null, docs_url: "https://example.invalid", oauth_supported: false,
      oauth_available: false, connection_mode: "credentials" }],
    connections: [{ id: "fixture-connection", provider: "amocrm", status: "active", auth_mode: "credentials", account_scoped: true,
      bound_to_project: true, binding_status: "ready", binding_config: {}, account_label: "", public_config: {}, capabilities: [],
      configured_fields: [], last_error: null, verified_at: null, last_checked_at: null, created_at: "2026-10-04", updated_at: "2026-10-04" }],
    recommended_pack: { key: "fixture", title: "", description: "", provider_keys: [], bound_count: 0, reusable_count: 0 },
  };
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client = queryClient;
  queryClient.setQueryData(["app-integrations", "fixture-project"], data); boundary.catalog.mockResolvedValue(data);
  await act(async () => root.render(createElement(QueryClientProvider, { client: queryClient }, createElement(FigmaIntegrationHub, {
    projectId: "fixture-project", projectName: "Локальная проверка", embedded: true,
  }))));
  await act(async () => Array.from(container.querySelectorAll("button")).find(button => button.textContent === "Добавить в приложение")!.click());
  return container.querySelector<HTMLTextAreaElement>("textarea")!;
}

it("shows a compact proposal beside the editable full text and sends the unchanged original payload", async () => {
  const textarea = await renderHub();
  expect(container.querySelector("h3")?.textContent).toBe("Добавить amoCRM");
  expect(textarea.value).toBe(originalAmoPrompt);
  expect(container.textContent).toContain("Оплата списывается после успешной сборки приложения.");
  expect(boundary.send).not.toHaveBeenCalled();
  await act(async () => Array.from(container.querySelectorAll("button")).find(button => button.textContent === "Запустить доработку")!.click());
  expect(boundary.send).toHaveBeenCalledTimes(1);
  expect(boundary.send.mock.calls[0][1]).toBe(originalAmoPrompt);
  expect(boundary.send.mock.calls[0][4]).toEqual({ skipClarify: true, idempotencyKey: expect.stringContaining("max-integration-fixture-project-") });
});

it("describes a failed attempt without claiming a legacy refund or a new upfront charge", async () => {
  await renderHub();
  boundary.send.mockResolvedValue({ run_id: "fixture-run", run_status: "failed" });
  await act(async () => Array.from(container.querySelectorAll("button")).find(button => button.textContent === "Запустить доработку")!.click());
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  expect(container.textContent).toContain("Предыдущая доработка завершилась без результата. Можно запустить новую попытку. Оплата — после успешной сборки.");
  expect(container.textContent).not.toContain("Новая попытка расходует баланс");
});

it("keeps editing and exact custom dispatch while removing the built-in summary", async () => {
  const textarea = await renderHub();
  const custom = originalAmoPrompt + "\nПокажи форму в разделе продаж.";
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea, custom);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(container.querySelector("h3")).toBeNull();
  expect(textarea.value).toBe(custom);
  expect(boundary.send).not.toHaveBeenCalled();
  await act(async () => Array.from(container.querySelectorAll("button")).find(button => button.textContent === "Запустить доработку")!.click());
  expect(boundary.send.mock.calls[0][1]).toBe(custom);
});
