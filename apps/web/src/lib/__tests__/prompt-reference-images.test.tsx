import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PromptInput } from "@/components/workspace/PromptInput";
import { ReferenceImageThumbnail } from "@/components/workspace/ReferenceImageThumbnail";
import { sendPrompt } from "@/lib/api/messages";

vi.mock("@/hooks/useVoiceInput", () => ({ useVoiceInput: () => ({ supported: false, state: "idle" }) }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
let root: Root;
let host: HTMLDivElement;
const image = { id: "image-1", filename: "screen.png", mime_type: "image/png", size_bytes: 4, width: 100, height: 100, url: "/api/projects/p/reference-images/image-1" };
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, headers: new Headers({ "content-type": "application/json" }), json: async () => image }));
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 100, height: 100, close() {} }));
  URL.createObjectURL = vi.fn(() => "blob:reference"); URL.revokeObjectURL = vi.fn();
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
async function mount(onSubmit = vi.fn().mockResolvedValue(false)) {
  await act(async () => root.render(<PromptInput projectId="p" onSubmit={onSubmit} onCancel={() => {}} onCancelPending={() => {}} isStreaming={false} pendingPrompt={null} />));
  return onSubmit;
}
async function add(kind: "picker" | "paste" | "drop", file = new File(["png!"], "screen.png", { type: "image/png" })) {
  if (kind === "picker") {
    const input = host.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull(); Object.defineProperty(input!, "files", { configurable: true, value: [file] });
    await act(async () => input!.dispatchEvent(new Event("change", { bubbles: true })));
  } else {
    const event = new Event(kind, { bubbles: true, cancelable: true });
    Object.defineProperty(event, kind === "paste" ? "clipboardData" : "dataTransfer", { value: { files: [file], items: [{ kind: "file", type: file.type, getAsFile: () => file }], types: ["Files"] } });
    await act(async () => host.querySelector("textarea")!.dispatchEvent(event));
  }
}
for (const kind of ["picker", "paste", "drop"] as const) {
  it(`${kind} uploads an image-only draft and retains stable image/key after refusal`, async () => {
    const submit = await mount(); await add(kind);
    expect(host.querySelector('img[alt="screen.png"]')).not.toBeNull();
    const send = [...host.querySelectorAll("button")].find((button) => button.textContent === "Отправить")!;
    expect(send.disabled).toBe(false);
    await act(async () => send.click()); await act(async () => send.click());
    expect(submit).toHaveBeenCalledTimes(2);
    expect(submit.mock.calls[0][0]).toBeTruthy();
    expect(submit.mock.calls[0][2]).toEqual(submit.mock.calls[1][2]);
    expect(submit.mock.calls[0][2]).toMatchObject({ referenceImages: [image], idempotencyKey: expect.any(String) });
    expect(host.querySelector('img[alt="screen.png"]')).not.toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
}
it("rejects unsupported images without uploading and leaves text paste untouched", async () => {
  await mount(); await add("paste", new File(["<svg/>"], "bad.svg", { type: "image/svg+xml" }));
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("PNG");
  expect(fetch).not.toHaveBeenCalled();
  const paste = new Event("paste", { bubbles: true, cancelable: true });
  Object.defineProperty(paste, "clipboardData", { value: { files: [], items: [{ kind: "string", type: "text/plain" }] } });
  host.querySelector("textarea")!.dispatchEvent(paste); expect(paste.defaultPrevented).toBe(false);
});
it("removes images and clears them only after acceptance", async () => {
  const submit = await mount(vi.fn().mockResolvedValue(true)); await add("picker");
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Удалить screen.png"]')!.click());
  expect(host.querySelector("img")).toBeNull();
  await add("picker");
  await act(async () => [...host.querySelectorAll("button")].find((button) => button.textContent === "Отправить")!.click());
  expect(submit).toHaveBeenCalledOnce(); expect(host.querySelector("img")).toBeNull();
});
it("retains the file after upload failure and sends only after a successful retry", async () => {
  const submit = await mount();
  vi.mocked(fetch).mockRejectedValueOnce(new Error("offline"));
  await add("picker");
  const send = [...host.querySelectorAll("button")].find((button) => button.textContent === "Отправить")!;
  expect(send.disabled).toBe(true); expect(host.querySelector("img")).not.toBeNull();
  await act(async () => [...host.querySelectorAll("button")].find((button) => button.textContent === "Повторить загрузку")!.click());
  expect(send.disabled).toBe(false);
  await act(async () => send.click()); expect(submit).toHaveBeenCalledOnce();
});
it("rejects oversized files and decoded dimensions before any upload", async () => {
  await mount();
  const big = new File(["x"], "big.png", { type: "image/png" }); Object.defineProperty(big, "size", { value: 5 * 1024 * 1024 + 1 });
  await add("picker", big); expect(fetch).not.toHaveBeenCalled();
  vi.mocked(createImageBitmap).mockResolvedValueOnce({ width: 9000, height: 2, close() {} } as ImageBitmap);
  await add("picker"); expect(fetch).not.toHaveBeenCalled();
});
it("sends immutable IDs only and preserves the supplied idempotency key", async () => {
  await sendPrompt("p", "use this", "model", [], { referenceImages: [image], idempotencyKey: "same-key" });
  const init = vi.mocked(fetch).mock.calls[0][1]!;
  expect(JSON.parse(init.body as string)).toMatchObject({ prompt: "use this", reference_image_ids: ["image-1"], idempotency_key: "same-key" });
  expect(init.body).not.toContain(image.url); expect(init.body).not.toContain(image.filename);
});
it("rehydrates history through authenticated project-scoped bytes, ignoring supplied URLs", async () => {
  vi.mocked(fetch).mockResolvedValue({ ok: true, blob: async () => new Blob(["png"], { type: "image/png" }) } as Response);
  await act(async () => root.render(<ReferenceImageThumbnail projectId="p" image={{ ...image, url: "https://untrusted.invalid/private" }} />));
  expect(fetch).toHaveBeenCalledWith("/api/projects/p/reference-images/image-1", expect.objectContaining({ credentials: "include" }));
  expect(host.querySelector("img")?.getAttribute("src")).toBe("blob:reference");
});
