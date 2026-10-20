import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { AccountProfile } from "@/components/account/AccountProfile";

const mocks = vi.hoisted(() => ({ exportAccount: vi.fn(), deleteAccount: vi.fn() }));
vi.mock("@/lib/api/account", () => mocks);
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));

it("downloads only on explicit action, prevents double submission and revokes its object URL", async () => {
  let resolve!: (data: Record<string, unknown>) => void;
  mocks.exportAccount.mockImplementation(() => new Promise(r => { resolve = r; }));
  const create = vi.fn((_blob: Blob) => "blob:synthetic-personal-data");
  const revoke = vi.fn();
  const previousCreate = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
  const previousRevoke = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
  Object.defineProperty(URL, "createObjectURL", { value: create, configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: revoke, configurable: true });
  const clicked: HTMLAnchorElement[] = [];
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { clicked.push(this); });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><AccountProfile email="own@example.invalid" /></QueryClientProvider>));
    expect(mocks.exportAccount).not.toHaveBeenCalled();
    const button = [...host.querySelectorAll("button")].find(b => b.textContent?.includes("Скачать личные данные"))!;
    await act(async () => button.click());
    await act(async () => { await vi.waitFor(() => expect(button.disabled).toBe(true)); });
    await act(async () => button.click());
    expect(mocks.exportAccount).toHaveBeenCalledTimes(1);
    vi.useFakeTimers();
    await act(async () => resolve({ scope: "personal_account_records", account: { email: "own@example.invalid" } }));
    expect(create).toHaveBeenCalledTimes(1);
    expect(create.mock.calls[0]?.[0]).toBeInstanceOf(Blob);
    expect(clicked).toHaveLength(1);
    expect(clicked[0].download).toBe("yleum-account-data.json");
    expect(clicked[0].getAttribute("href")).toBe("blob:synthetic-personal-data");
    expect(clicked[0].isConnected).toBe(false);
    expect(revoke).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTime(1000));
    expect(revoke).toHaveBeenCalledExactlyOnceWith("blob:synthetic-personal-data");
    expect(host.textContent).toContain("Файл личных данных подготовлен");
    expect(mocks.deleteAccount).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
    await act(async () => root.unmount());
    client.clear(); host.remove(); click.mockRestore();
    if (previousCreate) Object.defineProperty(URL, "createObjectURL", previousCreate);
    else Reflect.deleteProperty(URL, "createObjectURL");
    if (previousRevoke) Object.defineProperty(URL, "revokeObjectURL", previousRevoke);
    else Reflect.deleteProperty(URL, "revokeObjectURL");
  }
});
