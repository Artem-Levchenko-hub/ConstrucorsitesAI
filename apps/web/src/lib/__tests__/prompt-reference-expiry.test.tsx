import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PromptInput, type PromptReferenceSubmit } from "@/components/workspace/PromptInput";
import { usePromptStream } from "@/hooks/usePromptStream";
import type { SelectedElement } from "@/lib/api/types";

vi.mock("@/hooks/useVoiceInput", () => ({ useVoiceInput: () => ({ supported: false, state: "idle" }) }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn(), dismiss: vi.fn() } }));
class Socket {
  static OPEN = 1; static CONNECTING = 0; readyState = 1;
  onmessage = null; onclose = null; onopen = null;
  send() {} close() { this.readyState = 3; }
}
let host: HTMLDivElement; let root: Root; let client: QueryClient;
let uploads: File[]; let prompts: Array<{ reference_image_ids: string[]; idempotency_key: string }>;
let outcome: "expired" | "unknown" | "other404";
let rejectReupload: boolean;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
function Harness() {
  const stream = usePromptStream("p", "p");
  const submit = async (text: string, selections: SelectedElement[], references?: PromptReferenceSubmit) => {
    let expired = false;
    const accepted = await stream.submit(text, "model", selections, { ...references, onReferenceImagesExpired: () => { expired = true; } });
    return expired ? { accepted: false as const, reason: "reference_images_expired" as const } : accepted;
  };
  return <PromptInput projectId="p" onSubmit={submit} onCancel={stream.cancel} onCancelPending={stream.cancelPending} isStreaming={false} pendingPrompt={stream.pendingPrompt} />;
}
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 10, height: 10, close() {} }));
  URL.createObjectURL = vi.fn(() => "blob:draft"); URL.revokeObjectURL = vi.fn();
  uploads = []; prompts = []; outcome = "expired"; rejectReupload = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/reference-images")) {
      const file = (init!.body as FormData).get("file") as File; uploads.push(file);
      if (rejectReupload && uploads.length === 2) throw new Error("offline");
      return json({ id: `image-${uploads.length}`, filename: file.name, mime_type: file.type, size_bytes: file.size, width: 10, height: 10, url: "/private" });
    }
    if (url.endsWith("/prompt")) {
      prompts.push(JSON.parse(init!.body as string));
      if (outcome === "unknown") throw new Error("connection lost");
      return json({ error: { code: outcome === "expired" ? "reference_images_expired" : "not_found", message: "refused" } }, 404);
    }
    return json(null);
  }));
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  await act(async () => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>));
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.unstubAllGlobals(); });
async function attach() {
  const picker = host.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(picker, "files", { value: [new File(["png"], "draft.png", { type: "image/png" })] });
  await act(async () => picker.dispatchEvent(new Event("change", { bubbles: true })));
}
const sendButton = () => [...host.querySelectorAll("button")].find((button) => button.textContent === "Отправить")!;
const send = async () => { await act(async () => sendButton().click()); };

it("reuploads expired files after confirmed refusal and uses a new identity only on the next explicit submit", async () => {
  await attach(); await send();
  expect(uploads).toHaveLength(2); expect(uploads[1].name).toBe("draft.png"); expect(prompts).toHaveLength(1);
  expect(host.querySelector('img[alt="draft.png"]')).not.toBeNull();
  outcome = "unknown"; await send();
  expect(prompts[0].reference_image_ids).toEqual(["image-1"]); expect(prompts[1].reference_image_ids).toEqual(["image-2"]);
  expect(prompts[1].idempotency_key).not.toBe(prompts[0].idempotency_key);
});
for (const refusal of ["unknown", "other404"] as const) it(`${refusal} retains the original IDs and key without reupload`, async () => {
  outcome = refusal; await attach(); await send(); await send();
  expect(uploads).toHaveLength(1); expect(prompts).toHaveLength(2);
  expect(prompts[1]).toEqual(prompts[0]); expect(host.querySelector('img[alt="draft.png"]')).not.toBeNull();
});
it("keeps the original File when expired-image reupload fails and enables a safe upload retry", async () => {
  rejectReupload = true; await attach(); await send();
  expect(uploads).toHaveLength(2); expect(sendButton().disabled).toBe(true);
  expect(host.querySelector('img[alt="draft.png"]')).not.toBeNull();
  const retry = [...host.querySelectorAll("button")].find((button) => button.textContent === "Повторить загрузку")!;
  expect(retry).toBeDefined(); await act(async () => retry.click());
  expect(uploads).toHaveLength(3); expect(uploads[2].name).toBe("draft.png"); expect(uploads[2].size).toBe(3);
  expect(sendButton().disabled).toBe(false); expect(prompts).toHaveLength(1);
});
it("advertises the enforced five-megabyte file limit", () => {
  expect(host.querySelector('[aria-label="Прикрепить изображения"]')?.getAttribute("title")).toContain("по 5 МБ");
});
