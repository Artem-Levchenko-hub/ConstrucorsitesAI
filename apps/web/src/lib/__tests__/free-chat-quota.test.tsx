import { act } from "react";
import Link from "next/link";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatPanel } from "@/components/workspace/ChatPanel";
import { MaxStudio } from "@/components/max/MaxStudio";
import { OWNER_PROFILE_QUERY_KEY, refreshOwnerAfterAcceptedPrompt } from "@/lib/owner-chat-quota";
import type { Message, User } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({ profile: vi.fn(), submit: vi.fn(), messages: vi.fn(), create: vi.fn(), push: vi.fn() }));
vi.mock("@/lib/api/owner-profile", () => ({ getOwnerProfile: mocks.profile }));
vi.mock("@/hooks/usePromptStream", () => ({ usePromptStream: () => ({ submit: mocks.submit, cancel: vi.fn(), cancelPending: vi.fn(), pendingPrompt: null }) }));
vi.mock("@/lib/api/messages", () => ({ listMessages: mocks.messages }));
vi.mock("@/lib/api/max-studio", () => ({ getMaxProjectConfig: async () => ({ config_version: 1 }), getMaxReadiness: async () => ({ items: [] }), saveMaxProjectConfig: vi.fn() }));
vi.mock("@/lib/api/projects", () => ({ listProjects: async () => [{ id: "p", template: "max_miniapp", name: "Сохранённое приложение" }], createProject: mocks.create }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/components/max/MaxStudioHeader", () => ({ MaxStudioHeader: () => null }));
vi.mock("@/components/max/MaxStudioProjectCard", () => ({ MaxStudioProjectCard: () => <Link href="/max/p/dashboard">Открыть сохранённое приложение</Link> }));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), success: vi.fn(), warning: vi.fn(), error: vi.fn() } }));
vi.mock("@/components/workspace/ChatMessage", () => ({ ChatMessage: (props: { message: Message; onFix?: (s: string) => void; onSuggest?: (s: string) => void; onRetry?: () => void }) => <article>
  {props.message.content}{props.onFix && <button onClick={() => props.onFix?.("Почини")}>Починить</button>}
  {props.onSuggest && <button onClick={() => props.onSuggest?.("Ещё идея")}>Добавить идею</button>}
  {props.onRetry && <button onClick={props.onRetry}>Повторить вручную</button>}
</article> }));

const base: User = { id: "owner", email: "qa@example.invalid", created_at: "2026-10-04", last_login_at: null };
let profile: User, client: QueryClient, root: Root, container: HTMLDivElement;
const button = (text: string) => [...container.querySelectorAll("button")].find(b => b.textContent?.trim() === text);
async function render(kind: "chat" | "studio" = "chat") {
  await act(async () => root.render(<QueryClientProvider client={client}>{kind === "chat" ? <ChatPanel projectId="p" projectSlug="p" /> : <MaxStudio email={base.email} />}</QueryClientProvider>));
  await act(async () => { await vi.waitFor(() => expect(mocks.profile).toHaveBeenCalled()); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
}
async function draft(text: string) {
 const el=container.querySelector("textarea")!;
 await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value")!.set!.call(el,text);el.dispatchEvent(new Event("input",{bubbles:true})); });
 return el;
}
beforeEach(() => {
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});vi.clearAllMocks();sessionStorage.clear();window.history.replaceState(null,"","/max/p");
 profile={...base,user_chat_messages_limit:1,user_chat_messages_remaining:1};mocks.profile.mockImplementation(async()=>profile);mocks.messages.mockResolvedValue([]);mocks.submit.mockResolvedValue(true);
 client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});container=document.createElement("div");document.body.append(container);root=createRoot(container);
});
afterEach(async()=>{await act(async()=>root.unmount());client.clear();container.remove();sessionStorage.clear();window.history.replaceState(null,"","/");});

it("shows the account-wide Free1 allowance, then accepted first send refreshes to0 and blocks button/Enter/global shortcut",async()=>{
 mocks.submit.mockImplementation(async()=>{profile={...profile,user_chat_messages_remaining:0};refreshOwnerAfterAcceptedPrompt(client);return true;});
 await render();expect(container.textContent).toContain("Осталось сообщений: 1 из 1 на весь аккаунт");
 await draft("Создай кофейню");await act(async()=>button("Отправить")!.click());
 await act(async()=>{await vi.waitFor(()=>expect(container.textContent).toContain("Осталось сообщений: 0"));});
 expect(mocks.profile.mock.calls.length).toBeGreaterThan(1);expect(mocks.submit).toHaveBeenCalledTimes(1);
 const input=await draft("Второе сообщение");expect(button("Отправить")?.disabled).toBe(true);
 await act(async()=>{input.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}));window.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",ctrlKey:true,bubbles:true}));});
 expect(mocks.submit).toHaveBeenCalledTimes(1);expect(input.value).toBe("Второе сообщение");
 expect(container.querySelector('a[href="/billing/plan"]')?.textContent).toBe("Выбрать тариф");
});
it("Free0 keeps history and hides manual retry/fix/suggestion/discovery/survey alternatives",async()=>{
 profile={...profile,user_chat_messages_remaining:0};
 mocks.messages.mockResolvedValue([{id:"u",role:"user",content:"Моё первое описание"},{id:"a",role:"assistant",content:"Готовое приложение",tokens_out:0}]);
 client.setQueryData(["discovery-choices","p","a"],{choices:["Добавить ещё"],allowCustom:true,multiSelect:false});
 client.setQueryData(["onboarding-survey","p"],[{kind:"choice",message:"Что ещё?",choices:["Ещё"]}]);
 await render();await act(async()=>{await vi.waitFor(()=>expect(container.textContent).not.toContain("Что ещё?"));});expect(container.textContent).toContain("Моё первое описание");expect(container.textContent).toContain("Готовое приложение");
 expect(button("Повторить вручную")).toBeUndefined();expect(button("Починить")).toBeUndefined();expect(button("Добавить идею")).toBeUndefined();expect(container.textContent).not.toContain("Что ещё?");expect(container.textContent).not.toContain("Добавить ещё");expect(mocks.submit).not.toHaveBeenCalled();
});
it.each(["Pro","Business","no-plan"])("%s null quota leaves ordinary manual alternatives and sends unchanged",async()=>{
 profile={...base,user_chat_messages_limit:null,user_chat_messages_remaining:null};mocks.messages.mockResolvedValue([{id:"a",role:"assistant",content:"История",tokens_out:0}]);
 await render();expect(container.textContent).not.toContain("Осталось сообщений");expect(button("Починить")).toBeDefined();expect(button("Повторить вручную")).toBeDefined();
 await draft("Новое пожелание");await act(async()=>button("Отправить")!.click());expect(mocks.submit).toHaveBeenCalledWith("Новое пожелание","topmix-v1",[],undefined);
});
it("Free0 permits explicit stable starter replay after unknown outcome and sends the same idempotency key",async()=>{
 profile={...profile,user_chat_messages_remaining:0};sessionStorage.setItem("omnia:max:starter:p","Сохранённое первое описание");window.history.replaceState(null,"","/max/p?starter=1");client.setQueryData(OWNER_PROFILE_QUERY_KEY,profile);
 await render();await act(async()=>{await vi.waitFor(()=>expect(mocks.submit).toHaveBeenCalledTimes(1));});
 expect(mocks.submit).toHaveBeenCalledWith("Сохранённое первое описание","topmix-v1",[],{skipClarify:true,idempotencyKey:"max-starter-p"});
});
it("Free0 starter keeps saved apps accessible and disables creating a new authored brief",async()=>{
 profile={...profile,user_chat_messages_remaining:0};await render("studio");expect(button("Создать приложение")?.disabled).toBe(true);
 expect(container.querySelector('a[href="/max/p/dashboard"]')).not.toBeNull();expect(mocks.create).not.toHaveBeenCalled();expect(container.textContent).toContain("Готовое приложение можно настроить и опубликовать");
});


it.each(["pending", "error"])("starter keeps its same-key lost acceptance with %s profile when reopened at Free0", async (profileState) => {
  mocks.profile.mockImplementation(() => profileState === "pending"
    ? new Promise(() => {}) : Promise.reject(new Error("Профиль недоступен")));
  const storageKey = "omnia:max:starter:p";
  const prompt = "Первое описание приложения";
  sessionStorage.setItem(storageKey, prompt);
  window.history.replaceState(null, "", "/max/p?starter=1");
  let markerAtDispatch: string | null = null;
  mocks.submit.mockImplementationOnce(async () => {
    markerAtDispatch = sessionStorage.getItem(storageKey);
    return false; // Server accepted, but usePromptStream received no response.
  });
  await render();
  await act(async () => { await vi.waitFor(() => expect(mocks.submit).toHaveBeenCalledOnce()); });
  await act(async () => { await vi.waitFor(() => expect(client.getQueryState(OWNER_PROFILE_QUERY_KEY)?.status).toBe(profileState)); });
  expect(markerAtDispatch).toBe(prompt);
  expect(sessionStorage.getItem(storageKey)).toBe(prompt);
  expect(window.location.search).toBe("?starter=1");
  const key = mocks.submit.mock.calls[0][3].idempotencyKey;
  await act(async () => root.render(null));
  client.removeQueries({ queryKey: OWNER_PROFILE_QUERY_KEY });
  profile = { ...profile, user_chat_messages_remaining: 0 };
  mocks.profile.mockResolvedValue(profile);
  client.setQueryData(OWNER_PROFILE_QUERY_KEY, profile);
  await render();
  await act(async () => { await vi.waitFor(() => expect(mocks.submit).toHaveBeenCalledTimes(2)); });
  expect(mocks.submit.mock.calls[1][3].idempotencyKey).toBe(key);
  expect(key).toBe("max-starter-p");
  expect(sessionStorage.getItem(storageKey)).toBeNull();
  expect(window.location.search).toBe("");
  expect(button("Отправить")?.disabled).toBe(true);
});
