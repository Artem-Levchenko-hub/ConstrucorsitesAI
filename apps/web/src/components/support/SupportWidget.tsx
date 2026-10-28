"use client";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { useSupportEvents } from "@/hooks/useSupportEvents";
import { clearGuestSupportDrafts, clearSupportReply, clearSupportDraft, readSupportDraft, saveSupportDraft, clearGuestTickets, createSupport, guestCapability, listSupport, readGuestTickets, saveGuestTickets, supportContextOptions, type GuestTicket, type SupportContext, type SupportCreate } from "@/lib/api/support";
import { SupportThread } from "./SupportThread";
import { SupportAssistant } from "./SupportAssistant";
import "@/components/max/max-studio.css";
import "./support.css";

/** Global owner of identity checks, the support socket, and private cache cleanup. */
export function SupportWidget() {
  const pathname = usePathname();
  const client = useQueryClient();
  const context = useQuery(supportContextOptions);
  const adminCount = useQuery({ queryKey: ["support", context.data?.user_id, "new-count"], queryFn: ({ signal }) => listSupport("status=new&archived=false&limit=1", true, signal), enabled: !!context.data?.is_admin && !context.isError, retry: false, refetchInterval: 30_000 });
  const [open, setOpen] = useState(false);
  const [checking, setChecking] = useState(false);
  const previous = useRef<string | undefined>(undefined);
  const identity = context.data ? `${context.data.user_id ?? "guest"}:${context.data.is_admin}` : undefined;
  useEffect(() => {
    if (identity === undefined) return;
    if (previous.current !== undefined && previous.current !== identity) {
      void client.cancelQueries({ queryKey: ["support"] }); client.removeQueries({ queryKey: ["support"] }); clearGuestTickets(); clearSupportDraft(); clearSupportReply();
    }
    if (context.data?.user_id) { clearGuestTickets(); clearGuestSupportDrafts(); }
    previous.current = identity;
  }, [identity, client, context.data?.user_id]);
  useEffect(() => {
    let mounted = true;
    const verify = () => {
      setChecking(true);
      void client.fetchQuery({ ...supportContextOptions, staleTime: 0 }).finally(() => { if (mounted) setChecking(false); }).catch(() => {});
    };
    verify(); window.addEventListener("focus", verify);
    return () => { mounted = false; window.removeEventListener("focus", verify); };
  }, [pathname, client]);
  return <Dialog open={open} onOpenChange={setOpen}>
    <DialogTrigger asChild><button type="button" className="support-launcher" data-max-studio aria-label="Открыть поддержку" title="Поддержка Yleum">?{context.data?.is_admin && !context.isError && !context.isFetching && !!adminCount.data?.new_count && <span className="support-badge" aria-label={`${adminCount.data.new_count} новых обращений`}>{adminCount.data.new_count}</span>}</button></DialogTrigger>
    {context.data && <SupportSession key={identity} context={context.data} available={!context.isError} open={open} checking={checking} newCount={adminCount.data?.new_count ?? 0} />}
    {open && (context.isError || !context.data) && <DialogContent data-max-studio className="support-dialog"><DialogTitle>Поддержка Yleum</DialogTitle><DialogDescription>Напишите нам — сохраним обращение и ответим.</DialogDescription>{context.isError ? <p role="alert">Не удалось подключиться. <button onClick={() => void context.refetch()}>Повторить</button></p> : <p role="status">Подключаем поддержку…</p>}</DialogContent>}
  </Dialog>;
}

function SupportSession({ context, available, open, checking, newCount }: { context: SupportContext; available: boolean; open: boolean; checking: boolean; newCount: number }) {
  const client = useQueryClient();
  const scope = context.user_id ?? "guest";
  const [draft] = useState(() => readSupportDraft(scope));
  const [view, setView] = useState<"help" | "contact">(() => draft ? "contact" : "help");
  const [guests, setGuests] = useState<GuestTicket[]>(() => context.user_id ? [] : readGuestTickets());
  const [selected, setSelected] = useState<string | null>(null);
  const [subject, setSubject] = useState(draft?.subject ?? "");
  const [message, setMessage] = useState(draft?.message ?? "");
  const [email, setEmail] = useState(draft?.email ?? "");
  const [consent, setConsent] = useState(draft?.privacy_accepted ?? false);
  const [website, setWebsite] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [receipt, setReceipt] = useState("");
  const [offset, setOffset] = useState(0);
  const attempt = useRef<SupportCreate | null>(draft);
  const busy = useRef(false);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const selectedGuest = guests.find(item => item.id === selected);
  // Keep unsent form state through transient context failures, but grant no UI or live access.
  useSupportEvents(available ? context.user_id : null, available && context.is_admin, available ? selectedGuest : undefined);
  const history = useQuery({ queryKey: ["support", scope, "history", offset], queryFn: ({ signal }) => listSupport(`limit=50&offset=${offset}`, false, signal), enabled: available && open && !!context.user_id && !checking, retry: false });
  function contactOperator(question = "") {
    // A handoff may prefill a fresh form, never overwrite an existing draft or send it.
    if (question && !subject.trim() && !message.trim()) {
      setSubject("Вопрос к поддержке"); setMessage(question);
    }
    setView("contact");
  }
  async function submit() {
    if (!available || busy.current || !subject.trim() || !message.trim() || (!context.user_id && (!email.trim() || !consent))) return;
    busy.current = true; setPending(true); setError("");
    const content = { subject: subject.trim(), message: message.trim(), source_path: window.location.pathname.slice(0, 512), website, ...(!context.user_id ? { email: email.trim(), privacy_accepted: consent } : {}) };
    // Retain an identical retry's key and guest capability even when delivery is ambiguous.
    const prior = attempt.current;
    if (!prior || prior.subject !== content.subject || prior.message !== content.message || prior.email !== content.email || prior.website !== website) attempt.current = { ...content, request_id: crypto.randomUUID(), ...(!context.user_id ? { guest_token: prior?.guest_token ?? guestCapability() } : {}) };
    const payload = attempt.current!;
    saveSupportDraft(scope, payload);
    try {
      const ticket = await createSupport(payload);
      if (!active.current) return;
      if (payload.guest_token) {
        const items = [...guests.filter(item => item.id !== ticket.id), { id: ticket.id, reference: ticket.reference, token: payload.guest_token }];
        setGuests(items); saveGuestTickets(items);
      }
      client.setQueryData(["support", scope, "detail", ticket.id], ticket);
      setSelected(ticket.id); setReceipt(ticket.reference); setSubject(""); setMessage(""); attempt.current = null; clearSupportDraft();
      void client.invalidateQueries({ queryKey: ["support"] });
    } catch { setError("Не удалось отправить. Черновик сохранён — попробуйте ещё раз."); }
    finally { busy.current = false; setPending(false); }
  }
  if (!open || !available) return null;
  return <DialogContent data-max-studio className="support-dialog">
    <DialogTitle>Поддержка Yleum</DialogTitle><DialogDescription>Быстрые ответы на частые вопросы и связь с оператором.</DialogDescription>
    {context.is_admin && <a className="support-admin-link" href="/admin/max?tab=support">Обращения пользователей{newCount ? ` · ${newCount} новых` : ""}</a>}
    {checking && <p role="status">Проверяем доступ…</p>}<div hidden={checking} inert={checking}>
      {selected ? <><button className="support-back" type="button" onClick={() => { setSelected(null); setReceipt(""); }}>← Все обращения</button>{receipt && <p role="status" className="support-receipt">Обращение {receipt} принято. Переписка сохранена.</p>}<SupportThread key={selected} id={selected} scope={scope} token={selectedGuest?.token} /></> : <>
        <nav className="support-actions support-navigation" aria-label="Раздел поддержки">
          <button type="button" aria-pressed={view === "help"} onClick={() => setView("help")}>Автопомощник</button>
          <button type="button" aria-pressed={view === "contact"} onClick={() => contactOperator()}>Написать оператору</button>
        </nav>
        <div hidden={view !== "help"}><SupportAssistant onContact={contactOperator} /></div>
        <form hidden={view !== "contact"} className="support-form" onSubmit={event => { event.preventDefault(); void submit(); }}>
          <label>Тема<input aria-label="Тема обращения" maxLength={160} required value={subject} disabled={pending} onChange={event => setSubject(event.target.value)} placeholder="Например, не получается опубликовать" /></label>
          <label>Сообщение<textarea aria-label="Сообщение" maxLength={8000} required rows={5} value={message} disabled={pending} onChange={event => setMessage(event.target.value)} placeholder="Что вы делали и что произошло?" /></label>
          {!context.user_id && <><label>Email для ответа<input aria-label="Ваш email" type="email" autoComplete="email" maxLength={254} required value={email} disabled={pending} onChange={event => setEmail(event.target.value)} /></label><label className="support-consent"><input type="checkbox" required checked={consent} disabled={pending} onChange={event => setConsent(event.target.checked)} /><span>Согласен на обработку данных обращения для ответа службы поддержки. <a href="/legal/privacy" target="_blank" rel="noreferrer">Политика конфиденциальности</a></span></label><p className="support-meta">История доступна в этом браузере. Не очищайте данные сайта, чтобы продолжить переписку.</p></>}
          <label className="support-trap" aria-hidden="true">Сайт<input name="website" tabIndex={-1} autoComplete="off" value={website} onChange={event => setWebsite(event.target.value)} /></label>
          {error && <p role="alert" className="support-error">{error}</p>}
          <button className="support-primary" type="submit" disabled={pending}>{pending ? "Отправляем…" : "Отправить обращение"}</button>
        </form>
        <section className="support-history"><h3>Ваши обращения</h3>
          {history.isError && <p role="alert">Не удалось загрузить историю. <button onClick={() => void history.refetch()}>Повторить</button></p>}
          {context.user_id && history.isPending && <p role="status">Загружаем историю…</p>}
          {(context.user_id ? history.data?.items ?? [] : guests).map(item => <button type="button" key={item.id} onClick={() => setSelected(item.id)}>{item.reference}{"subject" in item ? ` · ${item.subject}` : ""}</button>)}
          {!(context.user_id ? history.data?.total : guests.length) && !history.isLoading && <p className="support-meta">Здесь появятся отправленные обращения.</p>}
          {context.user_id && (history.data?.total ?? 0) > 50 && <div className="support-actions"><button disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - 50))}>Назад</button><button disabled={offset + 50 >= (history.data?.total ?? 0)} onClick={() => setOffset(value => value + 50)}>Далее</button></div>}
        </section>
      </>}
    </div>
  </DialogContent>;
}
