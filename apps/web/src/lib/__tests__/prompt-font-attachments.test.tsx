import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PromptInput } from "@/components/workspace/PromptInput";
import { ChatMessage } from "@/components/workspace/ChatMessage";
import { sendPrompt } from "@/lib/api/messages";

vi.mock("@/hooks/useVoiceInput", () => ({ useVoiceInput: () => ({ supported: false, state: "idle" }) }));
vi.mock("@/lib/api/mocks", () => ({ USE_MOCKS: false }));
const asset = { id: "font-1", family: "Onest", filename: "brand.woff2", sha256: "a".repeat(64), size_bytes: 4, weight: 400, style: "normal" as const, source: "upload" as const, license: null };
const image = { id: "image-1", filename: "screen.png", mime_type: "image/png", size_bytes: 4, width: 1, height: 1, url: "/private" };
let root: Root; let host: HTMLDivElement;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  vi.stubGlobal("fetch", vi.fn(async (url) => ({ ok: true, status: 201, headers: new Headers({ "content-type": "application/json" }), json: async () => String(url).includes("reference-images") ? image : asset })));
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 1, height: 1, close() {} }));
  URL.createObjectURL = vi.fn(() => "blob:image"); URL.revokeObjectURL = vi.fn();
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
const button = (label: string) => [...host.querySelectorAll("button")].find(item => item.textContent === label)!;
async function mount(submit = vi.fn().mockResolvedValue(false), projectId = "p") {
  await act(async () => root.render(<PromptInput projectId={projectId} onSubmit={submit} onCancel={() => {}} onCancelPending={() => {}} isStreaming={false} pendingPrompt={null} />));
  return submit;
}
async function picker() {
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Прикрепить шрифт"]')?.click());
  const input = host.querySelector<HTMLInputElement>('input[aria-label="Выбрать файл шрифта"]');
  expect(input, "A dedicated font file picker is available").not.toBeNull();
  Object.defineProperty(input!, "files", { configurable: true, value: [new File(["font"], "brand.woff2")] });
  await act(async () => input!.dispatchEvent(new Event("change", { bubbles: true })));
}
for (const kind of ["drop", "paste"] as const) it(`splits mixed ${kind} into fonts and images without breaking image validation`, async () => {
  const submit = await mount();
  const files = [new File(["png"], "screen.png", { type: "image/png" }), new File(["font"], "brand.woff2")];
  const event = new Event(kind, { bubbles: true, cancelable: true });
  Object.defineProperty(event, kind === "paste" ? "clipboardData" : "dataTransfer", { value: { files, types: ["Files"], items: files.map(file => ({ kind: "file", type: file.type, getAsFile: () => file })) } });
  await act(async () => host.querySelector("textarea")!.dispatchEvent(event));
  expect(host.querySelector('img[alt="screen.png"]')).not.toBeNull();
  expect(host.querySelector('[aria-label="Шрифты к запросу"]')?.textContent).toContain("Onest");
  await act(async () => button("Отправить").click());
  expect(submit.mock.calls[0][2]).toMatchObject({ referenceImages: [image], fontSelections: [{ ...asset, role: "all" }] });
});
it("preserves font identity after unknown acceptance and clears only after acceptance", async () => {
  const submit = await mount(vi.fn().mockRejectedValueOnce(new Error("timeout")).mockResolvedValueOnce(false).mockResolvedValueOnce(true));
  await picker();
  for (let i=0;i<2;i++) await act(async () => button("Отправить").click());
  expect(submit).toHaveBeenCalledTimes(2);
  expect(submit.mock.calls[0][0]).toContain("шрифт");
  expect(submit.mock.calls[0][2]).toEqual(submit.mock.calls[1][2]);
  expect(host.querySelector('[aria-label="Шрифты к запросу"]')).not.toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => button("Отправить").click());
  expect(host.querySelector('[aria-label="Шрифты к запросу"]')).toBeNull();
});
it("keeps a failed upload retryable and blocks send until ready", async () => {
  const submit = await mount(); vi.mocked(fetch).mockRejectedValueOnce(new Error("offline"));
  await picker(); expect(button("Отправить").disabled).toBe(true);
  await act(async () => button("Повторить загрузку шрифта").click());
  expect(button("Отправить").disabled).toBe(false);
  await act(async () => button("Отправить").click()); expect(submit).toHaveBeenCalledOnce();
});
it("resolves a family link with the selected role and leaves normal text paste untouched", async () => {
  const submit = await mount();
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Прикрепить шрифт"]')?.click());
  const name = host.querySelector<HTMLInputElement>('[aria-label="Название шрифта или ссылка"]'); expect(name).not.toBeNull();
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!.call(name,"https://fonts.google.com/specimen/Lora"); name!.dispatchEvent(new Event("input", { bubbles:true })); });
  const role = host.querySelector<HTMLSelectElement>('[aria-label="Роль шрифта"]')!;
  await act(async () => { role.value="heading"; role.dispatchEvent(new Event("change",{bubbles:true})); button("Добавить шрифт").click(); });
  expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/projects/p/font-assets/resolve");
  expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string)).toEqual({family_name:"https://fonts.google.com/specimen/Lora"});
  await act(async () => button("Отправить").click());
  expect(submit.mock.calls[0][2]).toMatchObject({fontSelections:[{role:"heading"}]});
  const event=new Event("paste",{bubbles:true,cancelable:true});Object.defineProperty(event,"clipboardData",{value:{items:[{kind:"string",type:"text/plain"}],files:[]}});
  host.querySelector("textarea")!.dispatchEvent(event);expect(event.defaultPrevented).toBe(false);
});
it("sends font IDs and roles only and preserves the idempotency key", async () => {
  await sendPrompt("p","use font","model",[],{fontSelections:[{...asset,role:"heading"}],idempotencyKey:"stable-font-key"});
  const body=JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string);
  expect(body.font_selections).toEqual([{asset_id:"font-1",role:"heading"}]);
  expect(body.idempotency_key).toBe("stable-font-key");expect(JSON.stringify(body)).not.toContain("brand.woff2");
});
it("ignores a late upload from the old project and allows a new upload", async () => {
  await mount();let finish!: (value:Response)=>void;
  vi.mocked(fetch).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  await picker();await mount(vi.fn(),"other");
  await act(async()=>finish({ok:true,status:201,json:async()=>asset}as Response));
  expect(host.querySelector('[aria-label="Шрифты к запросу"]')).toBeNull();
  await picker();expect(host.querySelector('[aria-label="Шрифты к запросу"]')?.textContent).toContain("Onest");
  expect(String(vi.mocked(fetch).mock.calls.at(-1)![0])).toContain("/other/font-assets");
});
it("rejects oversized font files without uploading", async () => {
  await mount();
  const file=new File(["font"],"large.ttf");Object.defineProperty(file,"size",{value:8*1024*1024+1});
  const event=new Event("drop",{bubbles:true,cancelable:true});Object.defineProperty(event,"dataTransfer",{value:{files:[file],types:["Files"]}});
  await act(async()=>host.querySelector("textarea")!.dispatchEvent(event));
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("8 МБ");expect(fetch).not.toHaveBeenCalled();
});
it("renders persisted font metadata as history chips without downloading fonts", async () => {
  await act(async()=>root.render(<ChatMessage message={{id:"m",project_id:"p",snapshot_id:null,role:"user",content:"Примени шрифт",model_id:null,tokens_in:null,tokens_out:null,created_at:new Date().toISOString(),font_selections:[{...asset,role:"heading"}]}} />));
  expect(host.querySelector('[aria-label="Шрифты в сообщении"]')?.textContent).toContain("Onest");
  expect(host.textContent).toContain("Заголовки");expect(fetch).not.toHaveBeenCalled();
});
it("does not clear a new project's draft after an old submit finally accepts", async () => {
  let finish!:(value:boolean)=>void;
  const submit=await mount(vi.fn(()=>new Promise<boolean>(resolve=>{finish=resolve;})));
  await picker();await act(async()=>button("Отправить").click());expect(submit).toHaveBeenCalledOnce();
  await mount(vi.fn(),"other");await picker();
  await act(async()=>finish(true));
  expect(host.querySelector('[aria-label="Шрифты к запросу"]')?.textContent).toContain("Onest");
});
