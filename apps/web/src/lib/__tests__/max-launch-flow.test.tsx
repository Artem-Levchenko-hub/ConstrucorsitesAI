import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { MaxLaunchButton } from "@/components/max/MaxLaunchButton";
import type { DeployStatus } from "@/lib/api/types";

const deploy = (phase: DeployStatus["phase"], run_id: string | null): DeployStatus => ({
  phase, run_id, started_at: null, finished_at: null, prod_url: null,
  image_tag: null, error: null, detail: null, target_label: null, target_id: null,
  can_cancel: false, logs: [],
});
let sequence = 0;
let projectId: string;
let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let status: DeployStatus;
let calls: { path: string; method: string; body: unknown }[];
let pendingReadiness: Promise<Response> | undefined;
let postDeploy: (() => DeployStatus) | undefined;
let stallDeployRead: boolean;

beforeEach(() => {
  vi.useFakeTimers();
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  projectId = `launch-${++sequence}`;
  calls = [];
  pendingReadiness = undefined;
  postDeploy = undefined;
  stallDeployRead = false;
  status = deploy("queued", null);
  window.localStorage.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const path = new URL(url).pathname;
    const method = init.method ?? "GET";
    calls.push({ path, method, body: init.body ? JSON.parse(String(init.body)) : null });
    let response: unknown;
    if (path.endsWith("/max/readiness")) {
      if (pendingReadiness) return pendingReadiness;
      response = { ready_to_launch: false, progress: 67, items: ["business", "legal", "build", "bot", "publish", "max_url"].map(id => ({ id, done: !["publish", "max_url"].includes(id), blocking: true, label: id, action: null })) };
    } else if (path.endsWith("/runtime")) {
      response = { state: "running" };
    } else if (path.endsWith("/deploy")) {
      if (method === "GET" && stallDeployRead) return new Promise<Response>((_, reject) => {
        init.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
      });
      if (method === "POST") status = postDeploy ? postDeploy() : deploy("done", "new-release");
      response = status;
    } else if (path.endsWith("/activate")) {
      response = { eligible: true, connected: true, status: "active", bot_id: null, bot_name: null, bot_username: null, app_url: null, webhook_url: null, deep_link: null, last_error: null, verified_at: null, published_at: null };
    } else throw new Error(`Unexpected request ${method} ${path}`);
    return Response.json(response);
  }));
});

async function flush(ms = 1) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}
async function mount() {
  await act(async () => { root.render(<QueryClientProvider client={client}><MaxLaunchButton projectId={projectId} /></QueryClientProvider>); });
  await flush();
}
function button() { return container.querySelector<HTMLButtonElement>("button")!; }
async function click() { await act(async () => { button().click(); }); await flush(); }
const posts = () => calls.filter(call => call.path.endsWith("/deploy") && call.method === "POST");

afterEach(async () => {
  status = deploy("failed", "cleanup");
  await flush(2_001);
  await act(async () => root.unmount());
  client.clear();
  container.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("publishes once when legacy GET says queued but no operation exists", async () => {
  await mount();
  await click();
  expect(posts()).toHaveLength(1);
  expect(calls.some(call => call.path.endsWith("/activate"))).toBe(true);
  expect(calls.some(call => call.path.includes("/runtime"))).toBe(false);
  expect(button().disabled).toBe(false);
  expect(localStorage.getItem(`omnia:max:launch:${projectId}`)).toBeNull();
});

it("attaches to an actual queued run without creating another deployment", async () => {
  status = deploy("queued", "existing-release");
  await mount();
  await click();
  expect(posts()).toHaveLength(0);
  expect(button().disabled).toBe(true);
  status = deploy("done", "existing-release");
  await flush(2_001);
  expect(calls.some(call => call.path.endsWith("/activate"))).toBe(true);
  expect(button().disabled).toBe(false);
});

it("resumes the saved operation after a page reload without another POST", async () => {
  status = deploy("done", "saved-release");
  localStorage.setItem(`omnia:max:launch:${projectId}`, JSON.stringify({ version: 1, phase: "deploying", runId: "saved-release", idempotencyKey: "saved-key", deadlineAt: Date.now() + 60_000, paused: false }));
  await mount();
  expect(posts()).toHaveLength(0);
  expect(calls.some(call => call.path.endsWith("/activate"))).toBe(true);
  expect(localStorage.getItem(`omnia:max:launch:${projectId}`)).toBeNull();
});

it("ends an endlessly queued operation visibly when its saved deadline expires", async () => {
  status = deploy("queued", "stalled-release");
  localStorage.setItem(`omnia:max:launch:${projectId}`, JSON.stringify({ version: 1, phase: "deploying", runId: "stalled-release", idempotencyKey: "saved-key", deadlineAt: Date.now() + 5_000, paused: false }));
  await mount();
  await flush(5_001);
  expect(button().disabled).toBe(false);
  expect(container.querySelector('[role="alert"]')?.textContent).toBeTruthy();
  expect(posts()).toHaveLength(0);
});

it("does not resume publication before readiness has returned successfully", async () => {
  pendingReadiness = new Promise(() => {});
  localStorage.setItem(`omnia:max:launch:${projectId}`, "new");
  await mount();
  expect(calls.filter(call => !call.path.endsWith("/max/readiness"))).toHaveLength(0);
});

it("keeps the idempotency key when an accepted POST loses its response", async () => {
  postDeploy = () => { status = deploy("done", "accepted-release"); throw new Error("Connection lost"); };
  await mount();
  await click();
  expect(button().disabled).toBe(false);
  expect(container.querySelector('[role="alert"]')?.textContent).toBeTruthy();
  const originalKey = (posts()[0].body as { idempotency_key: string }).idempotency_key;
  postDeploy = () => status;
  await click();
  expect(posts()).toHaveLength(2);
  expect((posts()[1].body as { idempotency_key: string }).idempotency_key).toBe(originalKey);
  expect(calls.filter(call => call.path.endsWith("/activate"))).toHaveLength(1);
  expect(localStorage.getItem(`omnia:max:launch:${projectId}`)).toBeNull();
});

it("uses a fresh key only after a terminal failure and an explicit new click", async () => {
  status = deploy("failed", "failed-release");
  localStorage.setItem(`omnia:max:launch:${projectId}`, JSON.stringify({ version: 1, phase: "deploying", runId: "failed-release", idempotencyKey: "failed-key", deadlineAt: Date.now() + 60_000, paused: false }));
  await mount();
  expect(posts()).toHaveLength(0);
  expect(localStorage.getItem(`omnia:max:launch:${projectId}`)).toBeNull();
  await click();
  expect(posts()).toHaveLength(1);
  expect((posts()[0].body as { idempotency_key: string }).idempotency_key).not.toBe("failed-key");
  expect(calls.filter(call => call.path.endsWith("/activate"))).toHaveLength(1);
});

it("refuses to activate a different release while resuming a saved operation", async () => {
  status = deploy("done", "different-release");
  localStorage.setItem(`omnia:max:launch:${projectId}`, JSON.stringify({ version: 1, phase: "deploying", runId: "saved-release", idempotencyKey: "saved-key", deadlineAt: Date.now() + 60_000, paused: false }));
  await mount();
  expect(calls.some(call => call.path.endsWith("/activate"))).toBe(false);
  expect(posts()).toHaveLength(0);
  expect(button().disabled).toBe(false);
  expect(container.querySelector('[role="alert"]')?.textContent).toBeTruthy();
});

it.each([false, true])("recovers from a superseded operation only after an explicit new launch (during polling=%s)", async (duringPolling) => {
  status = duringPolling ? deploy("queued", "saved-release") : deploy("done", "different-release");
  localStorage.setItem(`omnia:max:launch:${projectId}`, JSON.stringify({ version: 1, phase: "deploying", runId: "saved-release", idempotencyKey: "superseded-key", deadlineAt: Date.now() + 60_000, paused: false }));
  await mount();
  if (duringPolling) {
    status = deploy("done", "different-release");
    await flush(2_001);
  }
  expect(posts()).toHaveLength(0);
  expect(calls.some(call => call.path.endsWith("/activate"))).toBe(false);
  expect(button().disabled).toBe(false);
  await click();
  expect(posts()).toHaveLength(1);
  expect((posts()[0].body as { idempotency_key: string }).idempotency_key).not.toBe("superseded-key");
  expect(calls.filter(call => call.path.endsWith("/activate"))).toHaveLength(1);
  expect(localStorage.getItem(`omnia:max:launch:${projectId}`)).toBeNull();
});

it("aborts a stuck HTTP request at the persisted deadline without issuing POST", async () => {
  stallDeployRead = true;
  localStorage.setItem(`omnia:max:launch:${projectId}`, JSON.stringify({ version: 1, phase: "deploying", runId: "stalled-release", idempotencyKey: "saved-key", deadlineAt: Date.now() + 5_000, paused: false }));
  await mount();
  await flush(5_001);
  expect(button().disabled).toBe(false);
  expect(posts()).toHaveLength(0);
  expect(container.querySelector('[role="alert"]')?.textContent).toBeTruthy();
});
