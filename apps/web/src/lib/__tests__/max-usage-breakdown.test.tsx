import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { MaxUsageBreakdown } from "@/components/max/MaxUsageBreakdown";
import type { MaxUsage } from "@/lib/api/types";

const projectId = "usage-render-project";
const queryKey = ["max-usage", projectId];
const unavailable = "Данные о расходе недоступны";
const payload: MaxUsage = {
  run_cost_rub: 12.34, total_cost_rub: 56.78, run_id: "run-usage",
  run_status: "completed",
  stages: [{ id: "native_agent", label: "Работа генератора", cost_rub: 12.34,
    calls: 2, tokens_in: 100, tokens_out: 50, cache_read_tokens: 10,
    cache_write_tokens: 0, retries: 1 }],
};

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  client = new QueryClient({ defaultOptions: { queries: {
    retry: false, gcTime: 0, staleTime: Infinity,
  } } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  container.remove();
  vi.unstubAllGlobals();
});

async function mount() {
  await act(async () => root.render(
    <QueryClientProvider client={client}><MaxUsageBreakdown projectId={projectId} /></QueryClientProvider>,
  ));
}

async function settle(check: () => void) {
  await vi.waitFor(async () => {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    check();
  });
}

function assertUnavailable() {
  expect(container.querySelector("summary")?.textContent).toContain(unavailable);
  const details = container.querySelector("details")!;
  details.open = true;
  expect(details.querySelector("section")?.textContent).toContain(unavailable);
  expect(details.textContent).not.toContain("₽");
  expect(details.textContent).not.toContain("private backend diagnostic");
}

it.each(["HTTP 401", "network"])("shows unavailable data instead of zero after initial %s failure", async failure => {
  if (failure === "HTTP 401") {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: {
      code: "unauthorized", message: "private backend diagnostic",
    } }), { status: 401, headers: { "Content-Type": "application/json" } }));
  } else {
    fetchMock.mockRejectedValue(new TypeError("private backend diagnostic"));
  }
  await mount();
  await settle(assertUnavailable);
  expect(client.getQueryState(queryKey)?.status).toBe("error");
  expect(client.getQueryData(queryKey)).toBeUndefined();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0][0]).toContain(`/api/projects/${projectId}/max/usage`);
});

it.each([payload, { ...payload, run_cost_rub: 0, total_cost_rub: 0, stages: [] }])(
  "marks retained cached costs unavailable when their refetch fails (%j)", async cached => {
    client.setQueryData(queryKey, cached);
    await mount();
    fetchMock.mockRejectedValue(new TypeError("private backend diagnostic"));
    await act(async () => { await client.refetchQueries({ queryKey }); });
    await settle(assertUnavailable);
    expect(client.getQueryState(queryKey)?.status).toBe("error");
    expect(client.getQueryData(queryKey)).toEqual(cached);
    assertUnavailable();
    expect(container.textContent).not.toContain("Работа генератора");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  },
);

it("keeps loading ellipses and renders real costs after a successful response", async () => {
  let resolve!: (response: Response) => void;
  fetchMock.mockReturnValue(new Promise<Response>(done => { resolve = done; }));
  await mount();
  expect(container.querySelector("summary")?.textContent).toContain("…");
  expect(container.textContent).not.toContain("₽");
  expect(container.textContent).not.toContain(unavailable);
  await act(async () => resolve(Response.json(payload)));
  await settle(() => expect(container.querySelector("summary")?.textContent).toContain("12,34 ₽"));
  expect(container.querySelector("section")?.textContent).toContain("56,78 ₽");
  expect(container.textContent).toContain("Работа генератора");
  expect(container.textContent).toContain("2 выз.");
  expect(container.textContent).toContain("из кеша 10");
  expect(container.textContent).toContain("повторов 1");
  expect(container.textContent).not.toContain(unavailable);
});

it("restores actual costs after a failed cached refetch recovers", async () => {
  client.setQueryData(queryKey, payload);
  await mount();
  fetchMock.mockRejectedValueOnce(new TypeError("offline"));
  await act(async () => { await client.refetchQueries({ queryKey }); });
  await settle(() => expect(container.querySelector("summary")?.textContent).toContain(unavailable));
  fetchMock.mockResolvedValueOnce(Response.json({ ...payload, run_cost_rub: 9.25 }));
  await act(async () => { await client.refetchQueries({ queryKey }); });
  await settle(() => expect(container.querySelector("summary")?.textContent).toContain("9,25 ₽"));
  expect(container.textContent).not.toContain(unavailable);
});

it("keeps a successful real zero-cost response hidden", async () => {
  fetchMock.mockResolvedValue(Response.json({ ...payload, run_cost_rub: 0, total_cost_rub: 0, stages: [] }));
  await mount();
  await settle(() => expect(container.querySelector("details")).toBeNull());
  expect(client.getQueryState(queryKey)?.status).toBe("success");
});
