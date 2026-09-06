import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { MaxStudio } from "@/components/max/MaxStudio";

const mocks = vi.hoisted(() => ({ create: vi.fn(), save: vi.fn(), push: vi.fn() }));
vi.mock("@/lib/api/projects", () => ({ createProject: mocks.create, listProjects: async () => [] }));
vi.mock("@/lib/api/max-studio", () => ({ saveMaxProjectConfig: mocks.save }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/app/(auth)/actions", () => ({ logoutAction: async () => {} }));
vi.mock("@/components/max/MaxStudioAccountDisclosure", () => ({ MaxStudioAccountDisclosure: () => null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

it("transfers the entire pasted brief from creation to the chat starter", async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mocks.create.mockResolvedValue({ id: "long-brief-qa" });
  mocks.save.mockResolvedValue({});
  const brief = "я".repeat(19_992) + "КОНЕЦ ТЗ";
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  const change = (input: HTMLTextAreaElement | HTMLInputElement, value: string) => {
    Object.getOwnPropertyDescriptor(input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  };
  try {
    await act(async () => { root.render(<QueryClientProvider client={client}><MaxStudio email="qa@example.ru" /></QueryClientProvider>); });
    await act(async () => { await vi.waitFor(() => expect([...container.querySelectorAll("button")].some(b => b.textContent?.includes("Создать приложение"))).toBe(true)); });
    await act(async () => { [...container.querySelectorAll("button")].find(b => b.textContent?.includes("Создать приложение"))!.click(); });
    const input = document.querySelector<HTMLTextAreaElement>("#max-project-idea")!;
    expect(input.maxLength).toBe(-1);
    await act(async () => { change(document.querySelector<HTMLInputElement>("#max-project-name")!, "Склад"); change(input, brief); });
    expect(input.value).toBe(brief);
    const submit = [...document.querySelectorAll("button")].find(b => b.textContent?.includes("Создать проект"))!;
    await act(async () => { change(input, "я".repeat(20_001)); });
    expect(submit.disabled).toBe(true);
    expect(input.value.length).toBe(20_001);
    await act(async () => { change(input, brief); });
    await act(async () => { submit.click(); });
    await act(async () => { await vi.waitFor(() => expect(mocks.push).toHaveBeenCalled()); });
    expect(mocks.save.mock.calls[0][1].summary).toBe(brief);
    expect(sessionStorage.getItem("omnia:max:starter:long-brief-qa")).toContain(brief);
    expect(mocks.push).toHaveBeenCalledWith("/max/long-brief-qa?starter=1");
  } finally {
    await act(async () => { root.unmount(); });
    container.remove(); client.clear(); sessionStorage.clear();
  }
});
