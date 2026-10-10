import { act, createElement, type ReactNode } from "react";
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { createRoot, type Root } from "react-dom/client";
import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// Execute the actual supplied/fixed product page. Only external SDK/network
// and UIKit rendering are substituted; no platform app consumes this fixture.
const sdk = {
  getYleumAppConfig: vi.fn(), getYleumIntegrations: vi.fn(),
  createYleumLead: vi.fn(), getYleumLeads: vi.fn(), getYleumLeadStatus: vi.fn(), getYleumLeadReceipt: vi.fn(),
};
const saveMaxConsent = vi.fn();
let session = { mode: "max", user: { id: "101" }, error: null };
class IntegrationError extends Error {
  constructor(message: string, readonly code: string | null, readonly status: number) {
    super(message);
  }
}
const ui = (tag: string) => (props: Record<string, unknown> & { children?: ReactNode }) => {
  const { children, variant: _v, size: _s, loading: _l, asChild: _a, ...attrs } = props;
  return createElement(tag, attrs, children);
};
function product() {
  const fixture = process.env.CRM_PATCH_BASELINE === "1" ? "before" : "after";
  const source = readFileSync(resolve(process.cwd(),
    `../../tools/qa/crm-preview-receipt-20261010/${fixture}/src/app/page.tsx`), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } });
  const productModule = { exports: {} as { default?: React.ComponentType } };
  const imports: Record<string, unknown> = {
    react: React, "react/jsx-runtime": jsxRuntime,
    "@maxhub/max-ui": { Typography: { Title: ui("div"), Body: ui("div") },
      Button: ui("button"), IconButton: ui("button"), Input: ui("input"),
      Textarea: ui("textarea"), Spinner: ui("span"), CellList: ui("section"),
      CellSimple: ({ title, subtitle, after }: { title: string; subtitle: string; after: ReactNode }) =>
        createElement("article", null, title, subtitle, after), },
    "lucide-react": new Proxy({}, { get: () => ui("span") }),
    "@/lib/omnia/integration-client": { ...sdk, YleumIntegrationError: IntegrationError },
    "@/lib/omnia/client": { saveMaxConsent },
    "@/components/MaxAppProvider": { useMaxApp: () => session },
  };
  new Function("require", "exports", "module", compiled.outputText)(
    (name: string) => { if (!(name in imports)) throw Error(`Unexpected import ${name}`); return imports[name]; },
    productModule.exports, productModule,
  );
  return productModule.exports.default!;
}

let root: Root;
let host: HTMLDivElement;
let App: React.ComponentType;
async function until(assertion: () => void) {
  await vi.waitFor(async () => {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    assertion();
  }, { timeout: 2000, interval: 10 });
}
async function mount() {
  host = document.createElement("div"); document.body.appendChild(host);
  root = createRoot(host); App = product();
  await act(async () => root.render(createElement(App)));
}
async function click(text: string) {
  const button = [...host.querySelectorAll("button")].find(b => b.textContent?.trim() === text);
  if (!button) throw Error(`Missing button: ${text}`);
  await act(async () => button.click());
}
async function fill() {
  for (const [placeholder, value] of [["Иван Иванов", "QA synthetic"], ["+7 (999) 000-00-00", "+79990000000"]]) {
    const input = host.querySelector(`input[placeholder="${placeholder}"]`)!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
}
beforeEach(() => {
  vi.resetAllMocks(); vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("crypto", webcrypto);
  sessionStorage.clear();
  session = { mode: "max", user: { id: "101" }, error: null };
  sdk.getYleumAppConfig.mockResolvedValue({ legal: { personal_data_consent: false } });
  sdk.getYleumIntegrations.mockResolvedValue({ providers: ["amocrm"] });
  sdk.getYleumLeads.mockResolvedValue({ items: [], has_more: false });
  sdk.getYleumLeadStatus.mockResolvedValue({ id: "2925005", provider: "amocrm" });
  sdk.createYleumLead.mockResolvedValue({ id: "2925005", provider: "amocrm", details_status: "recorded" });
});
afterEach(async () => {
  if (root) await act(async () => root.unmount()); host?.remove(); vi.unstubAllGlobals();
});

it("owner preview renders a readonly form without CRM status/history/writes", async () => {
  session.mode = "preview"; session.user.id = "preview";
  sdk.getYleumIntegrations.mockRejectedValue(new IntegrationError("Откройте приложение внутри MAX", null, 401));
  await mount();
  expect(host.querySelector('input[placeholder="Иван Иванов"]')).not.toBeNull();
  expect(host.textContent).toContain("Режим просмотра");
  await click("Отправить заявку"); await click("Мои заявки");
  expect(host.textContent).toContain("История доступна только внутри MAX");
  expect(sdk.getYleumIntegrations).not.toHaveBeenCalled();
  expect(sdk.getYleumLeads).not.toHaveBeenCalled();
  expect(sdk.createYleumLead).not.toHaveBeenCalled(); expect(saveMaxConsent).not.toHaveBeenCalled();
});

it("double click dispatches once and confirmed ID survives an F5 remount", async () => {
  let finish!: (value: unknown) => void;
  sdk.createYleumLead.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  await mount(); await fill();
  const button = [...host.querySelectorAll("button")].find(b => b.textContent?.trim() === "Отправить заявку")!;
  await act(async () => { button.click(); button.click(); });
  await until(() => expect(sdk.createYleumLead).toHaveBeenCalledTimes(1));
  await act(async () => finish({ id: "2925005", provider: "amocrm" }));
  await until(() => expect(host.textContent).toContain("2925005"));
  expect([...Array(sessionStorage.length)].map((_, i) => sessionStorage.getItem(sessionStorage.key(i)!)).join()).not.toContain("QA synthetic");
  await act(async () => root.unmount()); host.remove(); await mount();
  expect(sdk.getYleumLeadStatus).toHaveBeenCalledWith("2925005");
  expect(host.textContent).toContain("Заявка отправлена");
  expect(host.textContent).toContain("2925005");
});

it("a deliberate new submission after confirmed success uses a fresh identity", async () => {
  await mount(); await fill(); await click("Отправить заявку");
  await until(() => expect(host.textContent).toContain("Заявка отправлена"));
  const first = sdk.createYleumLead.mock.calls[0][0].idempotency_key;
  await click("Новая заявка"); await fill(); await click("Отправить заявку");
  await until(() => expect(sdk.createYleumLead).toHaveBeenCalledTimes(2));
  expect(sdk.createYleumLead.mock.calls[1][0].idempotency_key).not.toBe(first);
});

it("lost response retains identity across F5 for the same payload", async () => {
  sdk.createYleumLead.mockRejectedValueOnce(new Error("Lost response"));
  await mount(); await fill(); await click("Отправить заявку");
  await until(() => expect(host.textContent).toContain("Lost response"));
  const first = sdk.createYleumLead.mock.calls[0][0].idempotency_key;
  await act(async () => root.unmount()); host.remove(); await mount();
  await fill(); await click("Отправить заявку");
  await until(() => expect(sdk.createYleumLead).toHaveBeenCalledTimes(2));
  expect(sdk.createYleumLead.mock.calls[1][0].idempotency_key).toBe(first);
});

it("an unknown outcome cannot be replaced by another payload or reset by a later 422", async () => {
  sdk.createYleumLead.mockRejectedValueOnce(new Error("Lost response"));
  await mount(); await fill(); await click("Отправить заявку");
  await until(() => expect(host.textContent).toContain("Lost response"));
  const oldKey = sdk.createYleumLead.mock.calls[0][0].idempotency_key;
  const input = host.querySelector('input[placeholder="Иван Иванов"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "Other QA");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click("Отправить заявку");
  await until(() => expect(host.textContent).toContain("Изменять данные"));
  expect(sdk.createYleumLead).toHaveBeenCalledTimes(1);
  await fill();
  sdk.createYleumLead.mockRejectedValueOnce(new IntegrationError("Rejected", "integration_request_rejected", 422));
  await click("Отправить заявку");
  await until(() => expect(host.textContent).toContain("Rejected"));
  sdk.createYleumLead.mockResolvedValueOnce({ id: "2925005", provider: "amocrm" });
  await click("Отправить заявку");
  await until(() => expect(sdk.createYleumLead).toHaveBeenCalledTimes(3));
  expect(sdk.createYleumLead.mock.calls[2][0].idempotency_key).toBe(oldKey);
});

it("fresh confirmed 422 rejection permits corrected input with a new identity", async () => {
  sdk.createYleumLead.mockRejectedValueOnce(new IntegrationError("Rejected", "integration_request_rejected", 422));
  await mount(); await fill(); await click("Отправить заявку");
  await until(() => expect(host.textContent).toContain("Заявка отклонена"));
  const key = sdk.createYleumLead.mock.calls[0][0].idempotency_key;
  await click("Отправить заявку");
  await until(() => expect(sdk.createYleumLead).toHaveBeenCalledTimes(2));
  expect(sdk.createYleumLead.mock.calls[1][0].idempotency_key).not.toBe(key);
});

it("JSON false or an unscoped legacy identity fail closed without a new CRM write", async () => {
  sessionStorage.setItem("lead_idempotency_key", "legacy-unknown");
  await mount();
  expect(host.textContent).toContain("Отправка заблокирована");
  expect(sdk.createYleumLead).not.toHaveBeenCalled();
  await act(async () => root.unmount()); host.remove(); sessionStorage.clear();
  sessionStorage.setItem(`yleum:amocrm:lead:v1:${location.origin}:101:max`, "false");
  await mount();
  expect(host.textContent).toContain("Отправка заблокирована");
  expect(sdk.createYleumLead).not.toHaveBeenCalled();
});

it("A to B to A ignores the first A history completion", async () => {
  let old!: (value: unknown) => void;
  sdk.getYleumLeads.mockReturnValueOnce(new Promise(resolve => { old = resolve; }));
  await mount(); await click("Мои заявки");
  session = { ...session, user: { id: "202" } };
  await act(async () => root.render(createElement(App)));
  session = { ...session, user: { id: "101" } };
  await act(async () => root.render(createElement(App)));
  await act(async () => old({ items: [{ id: "1", name: "Stale first A" }], has_more: false }));
  await click("Мои заявки");
  expect(host.textContent).not.toContain("Stale first A");
});

it("a late first A submit cannot overwrite the second A's newer unknown intent", async () => {
  let first!: (value: unknown) => void;
  let last!: (reason: Error) => void;
  sdk.createYleumLead.mockReturnValueOnce(new Promise(resolve => { first = resolve; }))
    .mockResolvedValueOnce({ id: "2925005", provider: "amocrm" })
    .mockReturnValueOnce(new Promise((_, reject) => { last = reject; }));
  await mount(); await fill(); await click("Отправить заявку");
  await until(() => expect(sdk.createYleumLead).toHaveBeenCalledTimes(1));
  session = { ...session, user: { id: "202" } };
  await act(async () => root.render(createElement(App)));
  session = { ...session, user: { id: "101" } };
  await act(async () => root.render(createElement(App)));
  await fill(); await click("Отправить заявку");
  await until(() => expect(host.textContent).toContain("Заявка отправлена"));
  await click("Новая заявка"); await fill(); await click("Отправить заявку");
  await until(() => expect(sdk.createYleumLead).toHaveBeenCalledTimes(3));
  const key2 = sdk.createYleumLead.mock.calls[2][0].idempotency_key;
  await act(async () => first({ id: "1001", provider: "amocrm" }));
  const storageKey = `yleum:amocrm:lead:v1:${location.origin}:101:max`;
  expect(JSON.parse(sessionStorage.getItem(storageKey)!)).toMatchObject({ state: "pending", key: key2 });
  await act(async () => last(new Error("Lost second response")));
  await act(async () => root.unmount()); host.remove(); await mount();
  expect(host.textContent).not.toContain("Заявка отправлена");
  expect(JSON.parse(sessionStorage.getItem(storageKey)!)).toMatchObject({ state: "pending", key: key2 });
});

it("unconfirmed comment/source warning is visible after create, F5 and history", async () => {
  const details = { id: "2925005", provider: "amocrm", details_status: "unknown", warning: "Комментарий не подтверждён" };
  sdk.createYleumLead.mockResolvedValueOnce(details);
  sdk.getYleumLeadStatus.mockResolvedValue(details);
  sdk.getYleumLeads.mockResolvedValue({ items: [{ ...details, name: "QA", status_name: "Новый", updated_at: 1, checked_at: "2026-10-10T00:00:00Z" }], has_more: false });
  await mount(); await fill(); await click("Отправить заявку");
  await until(() => expect(host.textContent).toContain("Заявка отправлена"));
  expect(host.textContent).toContain("Комментарий не подтверждён");
  await act(async () => root.unmount()); host.remove(); await mount();
  expect(host.textContent).toContain("Комментарий не подтверждён");
  await click("Перейти к моим заявкам");
  expect(host.textContent).toContain("Комментарий не подтверждён");
});

it("a malformed numeric result ID is not cached or shown as a confirmed success", async () => {
  sdk.createYleumLead.mockResolvedValueOnce({ id: 2925005, provider: "amocrm" });
  await mount(); await fill(); await click("Отправить заявку");
  await until(() => expect(host.textContent).toContain("Не удалось подтвердить ID"));
  expect(host.textContent).not.toContain("Заявка отправлена");
});

it("visibility return refreshes and listener cleanup prevents background reads", async () => {
  await mount(); await click("Мои заявки");
  const visible = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  expect(sdk.getYleumLeads).toHaveBeenCalledTimes(2);
  await click("Заявка");
  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(sdk.getYleumLeads).toHaveBeenCalledTimes(2); visible.mockRestore();
});

it("history refreshes on focus and ignores an older out-of-order reply", async () => {
  let old!: (value: unknown) => void;
  sdk.getYleumLeads.mockReturnValueOnce(new Promise(resolve => { old = resolve; }));
  await mount(); await click("Мои заявки");
  sdk.getYleumLeads.mockResolvedValueOnce({ items: [{ id: "2", name: "Current QA", status_name: "Переговоры", updated_at: 1, checked_at: "2026-10-10T00:00:00Z" }], has_more: true });
  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(host.textContent).toContain("Current QA"); expect(host.textContent).toContain("последние 20");
  await act(async () => old({ items: [{ id: "1", name: "Old QA" }], has_more: false }));
  expect(host.textContent).not.toContain("Old QA");
});

it("changing actor clears private UI and stale A promises cannot repopulate B", async () => {
  let old!: (value: unknown) => void;
  sdk.getYleumLeads.mockReturnValueOnce(new Promise(resolve => { old = resolve; }));
  await mount(); await click("Мои заявки");
  session = { ...session, user: { id: "202" } };
  await act(async () => root.render(createElement(App)));
  await act(async () => old({ items: [{ id: "1", name: "Private A" }], has_more: false }));
  expect(host.textContent).not.toContain("Private A");
});

it("storage failure fails closed before any CRM dispatch", async () => {
  await mount(); await fill();
  const failing = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw Error("Storage unavailable"); });
  await click("Отправить заявку");
  await until(() => expect(host.querySelector('[role="alert"]')).not.toBeNull());
  expect(sdk.createYleumLead).not.toHaveBeenCalled(); failing.mockRestore();
});

it("a cached receipt is not a success until the scoped server read validates it", async () => {
  await mount(); await fill(); await click("Отправить заявку");
  await until(() => expect(host.textContent).toContain("Заявка отправлена"));
  await act(async () => root.unmount()); host.remove();
  sdk.getYleumLeadStatus.mockRejectedValue(new IntegrationError("Denied", null, 404));
  await mount();
  expect(host.textContent).not.toContain("Заявка отправлена");
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
});

const legacyKey = "c68421c9-934a-48f8-b293-147d5197a8b9";
const scopedKey = () => `yleum:amocrm:lead:v1:${location.origin}:101:max`;
const legacyReceipt = () => ({ provider: "amocrm", status: "succeeded", id: "2964609",
  idempotency_key: legacyKey, request_digest: "a".repeat(64), details_status: "recorded" });

it("authenticated legacy adoption restores success and preserves old key across F5", async () => {
  sessionStorage.setItem("lead_idempotency_key", legacyKey);
  sdk.getYleumLeadReceipt.mockResolvedValue(legacyReceipt());
  sdk.getYleumLeadStatus.mockResolvedValue({ id: "2964609", provider: "amocrm" });
  await mount();
  await until(() => expect(host.textContent).toContain("2964609"));
  expect(sdk.getYleumLeadReceipt).toHaveBeenCalledWith(legacyKey, undefined);
  expect(sessionStorage.getItem("lead_idempotency_key")).toBe(legacyKey);
  expect(sdk.createYleumLead).not.toHaveBeenCalled(); expect(saveMaxConsent).not.toHaveBeenCalled();
  await act(async () => root.unmount()); host.remove(); await mount();
  await until(() => expect(host.textContent).toContain("2964609"));
  expect(sdk.getYleumLeadReceipt).toHaveBeenLastCalledWith(legacyKey,
    { requestDigest: "a".repeat(64), leadId: "2964609" });
});

it("explicit new intent after adoption survives F5 without replacing unknown outcome", async () => {
  sessionStorage.setItem("lead_idempotency_key", legacyKey);
  sdk.getYleumLeadReceipt.mockResolvedValue(legacyReceipt());
  sdk.getYleumLeadStatus.mockResolvedValue({ id: "2964609", provider: "amocrm" });
  await mount(); await until(() => expect(host.textContent).toContain("2964609"));
  await click("Новая заявка");
  sdk.createYleumLead.mockRejectedValueOnce(new Error("New lost response"));
  await fill(); await click("Отправить заявку");
  await until(() => expect(sdk.createYleumLead).toHaveBeenCalledTimes(1));
  const newKey = sdk.createYleumLead.mock.calls[0][0].idempotency_key;
  expect(newKey).not.toBe(legacyKey);
  await act(async () => root.unmount()); host.remove(); await mount();
  await until(() => expect(host.textContent).toContain("Исход предыдущей отправки неизвестен"));
  expect(JSON.parse(sessionStorage.getItem(scopedKey())!)).toMatchObject({ key: newKey, state: "pending" });
  expect(sessionStorage.getItem("lead_idempotency_key")).toBe(legacyKey);
  expect(host.textContent).not.toContain("Заявка отправлена");
});

it.each(["unknown", "rejected", "multiple", "foreign"])("legacy %s lookup fails closed", async () => {
  sessionStorage.setItem("lead_idempotency_key", legacyKey);
  sdk.getYleumLeadReceipt.mockRejectedValue(new IntegrationError("Unconfirmed", "integration_receipt_unconfirmed", 409));
  await mount(); await until(() => expect(host.textContent).toContain("Отправка заблокирована"));
  expect(sdk.createYleumLead).not.toHaveBeenCalled(); expect(sessionStorage.getItem(scopedKey())).toBeNull();
  expect(sessionStorage.getItem("lead_idempotency_key")).toBe(legacyKey);
});

it("legacy marker mismatch and malformed marker never adopt or dispatch", async () => {
  sessionStorage.setItem("lead_idempotency_key", legacyKey);
  sessionStorage.setItem(`${scopedKey()}:legacy-adoption`, JSON.stringify({ version: 1,
    key: legacyKey, id: "2964609", requestDigest: "b".repeat(64) }));
  sdk.getYleumLeadReceipt.mockResolvedValue(legacyReceipt());
  await mount(); await until(() => expect(host.textContent).toContain("Отправка заблокирована"));
  expect(sessionStorage.getItem(scopedKey())).toBeNull(); expect(sdk.createYleumLead).not.toHaveBeenCalled();
  await act(async () => root.unmount()); host.remove();
  sessionStorage.setItem(`${scopedKey()}:legacy-adoption`, "false");
  await mount(); await until(() => expect(host.textContent).toContain("Отправка заблокирована"));
});

it("partial adoption storage failure blocks then recovers without deleting legacy key", async () => {
  sessionStorage.setItem("lead_idempotency_key", legacyKey);
  sdk.getYleumLeadReceipt.mockResolvedValue(legacyReceipt());
  sdk.getYleumLeadStatus.mockResolvedValue({ id: "2964609", provider: "amocrm" });
  const original = Storage.prototype.setItem;
  const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function(this: Storage, key: string, value: string) {
    if (key.endsWith(":legacy-adoption")) throw Error("Storage full");
    original.call(this, key, value);
  });
  await mount(); await until(() => expect(host.textContent).toContain("Отправка заблокирована"));
  expect(sdk.createYleumLead).not.toHaveBeenCalled(); spy.mockRestore();
  await act(async () => root.unmount()); host.remove(); await mount();
  await until(() => expect(host.textContent).toContain("2964609"));
});

it("late actor A adoption cannot overwrite the later A unknown intent", async () => {
  let first!: (value: unknown) => void;
  sessionStorage.setItem("lead_idempotency_key", legacyKey);
  sdk.getYleumLeadReceipt.mockReturnValueOnce(new Promise(resolve => { first = resolve; }))
    .mockRejectedValueOnce(Error("Foreign" )).mockResolvedValue(legacyReceipt());
  sdk.getYleumLeadStatus.mockResolvedValue({ id: "2964609", provider: "amocrm" });
  await mount(); await until(() => expect(sdk.getYleumLeadReceipt).toHaveBeenCalledTimes(1));
  session = { ...session, user: { id: "202" } }; await act(async () => root.render(createElement(App)));
  await until(() => expect(sdk.getYleumLeadReceipt).toHaveBeenCalledTimes(2));
  session = { ...session, user: { id: "101" } }; await act(async () => root.render(createElement(App)));
  await until(() => expect(host.textContent).toContain("2964609")); await click("Новая заявка");
  sdk.createYleumLead.mockRejectedValueOnce(Error("Lost")); await fill(); await click("Отправить заявку");
  await until(() => expect(sdk.createYleumLead).toHaveBeenCalledTimes(1));
  const unknown = sessionStorage.getItem(scopedKey());
  await act(async () => first(legacyReceipt()));
  expect(sessionStorage.getItem(scopedKey())).toBe(unknown);
});

it("late same-key marker ID or digest changes block a deliberate new dispatch", async () => {
  sessionStorage.setItem("lead_idempotency_key", legacyKey);
  sdk.getYleumLeadReceipt.mockResolvedValue(legacyReceipt());
  sdk.getYleumLeadStatus.mockResolvedValue({ id: "2964609", provider: "amocrm" });
  await mount(); await until(() => expect(host.textContent).toContain("2964609"));
  const marker = JSON.parse(sessionStorage.getItem(`${scopedKey()}:legacy-adoption`)!);
  sessionStorage.setItem(`${scopedKey()}:legacy-adoption`, JSON.stringify({ ...marker, requestDigest: "b".repeat(64) }));
  await click("Новая заявка");
  expect(host.textContent).toContain("Заявка отправлена");
  expect(sdk.createYleumLead).not.toHaveBeenCalled();
});

it("F5 recovery cannot show old success or delete a newer unknown scoped receipt", async () => {
  let finish!: (value: unknown) => void;
  const saved = { version: 1, key: legacyKey, state: "succeeded", id: "2964609", fingerprint: "a".repeat(64) };
  sessionStorage.setItem(scopedKey(), JSON.stringify(saved));
  sdk.getYleumLeadStatus.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  await mount(); await until(() => expect(sdk.getYleumLeadStatus).toHaveBeenCalledTimes(1));
  const newer = { version: 1, key: "c68421c9-934a-48f8-b293-147d5197a8ba", state: "pending", attempted: true, fingerprint: "b".repeat(64) };
  sessionStorage.setItem(scopedKey(), JSON.stringify(newer));
  await act(async () => finish({ id: "2964609", provider: "amocrm" }));
  expect(host.textContent).not.toContain("Заявка отправлена");
  expect(host.textContent).toContain("Отправка заблокирована");
  expect(JSON.parse(sessionStorage.getItem(scopedKey())!)).toEqual(newer);
});

it("new submission cannot erase a different pending identity stored after success", async () => {
  sessionStorage.setItem(scopedKey(), JSON.stringify({ version: 1, key: legacyKey, state: "succeeded", id: "2964609", fingerprint: "a".repeat(64) }));
  sdk.getYleumLeadStatus.mockResolvedValue({ id: "2964609", provider: "amocrm" });
  await mount(); await until(() => expect(host.textContent).toContain("2964609"));
  const newer = { version: 1, key: "c68421c9-934a-48f8-b293-147d5197a8ba", state: "pending", attempted: true, fingerprint: "b".repeat(64) };
  sessionStorage.setItem(scopedKey(), JSON.stringify(newer));
  await click("Новая заявка");
  expect(JSON.parse(sessionStorage.getItem(scopedKey())!)).toEqual(newer);
});

it("late consent completion cannot overwrite or dispatch a newer unknown operation", async () => {
  let finish!: () => void;
  sdk.getYleumAppConfig.mockResolvedValue({ legal: { personal_data_consent: true } });
  saveMaxConsent.mockReturnValueOnce(new Promise<void>(resolve => { finish = resolve; }));
  await mount(); await fill();
  await act(async () => host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
  await click("Отправить заявку"); await until(() => expect(saveMaxConsent).toHaveBeenCalledTimes(1));
  const newer = { version: 1, key: legacyKey, state: "pending", attempted: true, fingerprint: "b".repeat(64) };
  sessionStorage.setItem(scopedKey(), JSON.stringify(newer));
  await act(async () => finish());
  expect(JSON.parse(sessionStorage.getItem(scopedKey())!)).toEqual(newer);
  expect(sdk.createYleumLead).not.toHaveBeenCalled();
});

it("late fresh422 cannot delete a newer unknown operation", async () => {
  let reject!: (value: unknown) => void;
  sdk.createYleumLead.mockReturnValueOnce(new Promise((_, failed) => { reject = failed; }));
  await mount(); await fill(); await click("Отправить заявку");
  await until(() => expect(sdk.createYleumLead).toHaveBeenCalledTimes(1));
  const newer = { version: 1, key: legacyKey, state: "pending", attempted: true, fingerprint: "b".repeat(64) };
  sessionStorage.setItem(scopedKey(), JSON.stringify(newer));
  await act(async () => reject(new IntegrationError("Rejected", "integration_request_rejected", 422)));
  expect(JSON.parse(sessionStorage.getItem(scopedKey())!)).toEqual(newer);
});
