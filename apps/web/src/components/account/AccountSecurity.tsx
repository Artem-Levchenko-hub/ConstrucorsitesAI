"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Laptop, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { listSessions, revokeSession, type AuthSession } from "@/lib/api/account";
import { changeAccountPassword, getAccountCredentials } from "@/lib/api/account-credentials";
import { date, QueryError } from "./account-presentation";

function device(value: string | null) {
  if (!value) return "Неизвестное устройство";
  if (/iPhone|iPad/i.test(value)) return "iPhone или iPad";
  if (/Android/i.test(value)) return "Android";
  if (/Macintosh/i.test(value)) return "Mac";
  if (/Windows/i.test(value)) return "Windows";
  return "Браузер";
}
function PasswordChangeForm({ email }: { email: string }) {
  const client = useQueryClient();
  const router = useRouter();
  const credentials = useQuery({ queryKey: ["account-credentials", email], queryFn: getAccountCredentials });
  const active = useRef(false);
  const submitting = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    if (submitting.current || !form.reportValidity()) return;
    const values = new FormData(form);
    const password = String(values.get("new_password") ?? "");
    if (password !== values.get("confirm_password")) {
      setError(new Error("Пароли не совпадают")); return;
    }
    if (!/\p{Decimal_Number}/u.test(password)) {
      setError(new Error("Пароль должен содержать хотя бы одну цифру")); return;
    }
    submitting.current = true; setPending(true); setError(null);
    try {
      await changeAccountPassword(String(values.get("current_password") ?? ""), password);
      if (!active.current) return;
      form.reset();
      // The server has already revoked sessions and cleared this session's cookies.
      // Do not send another logout request that could target a newly signed-in account.
      client.clear(); router.replace("/login"); router.refresh();
    } catch (value) {
      if (active.current) setError(value instanceof Error ? value : new Error("Не удалось изменить пароль"));
    } finally {
      submitting.current = false;
      if (active.current) setPending(false);
    }
  }
  return <section className="account-panel">
    <h2>Пароль</h2>
    <QueryError error={credentials.error} retry={() => void credentials.refetch()} />
    {credentials.isPending && <p role="status">Проверяем способы входа…</p>}
    {credentials.data?.has_password === false && <p className="account-hint">У аккаунта пока нет пароля. <Link href="/forgot-password" className="underline">Установить пароль через восстановление доступа</Link>.</p>}
    {credentials.data?.has_password && <form onSubmit={submit} className="space-y-4">
      <p className="account-hint">После смены пароля все сессии завершатся. Войдите снова с новым паролем.</p>
      <div className="space-y-2"><label htmlFor="account-current-password">Текущий пароль</label>
        <Input id="account-current-password" name="current_password" type="password" autoComplete="current-password" required maxLength={128} disabled={pending} /></div>
      <div className="space-y-2"><label htmlFor="account-new-password">Новый пароль</label>
        <Input id="account-new-password" name="new_password" type="password" autoComplete="new-password" required minLength={8} maxLength={128} disabled={pending} />
        <p className="account-hint">От 8 до 128 символов, хотя бы одна цифра.</p></div>
      <div className="space-y-2"><label htmlFor="account-confirm-password">Повторите новый пароль</label>
        <Input id="account-confirm-password" name="confirm_password" type="password" autoComplete="new-password" required minLength={8} maxLength={128} disabled={pending} /></div>
      <QueryError error={error} />
      <Button type="submit" disabled={pending}>{pending ? "Сохраняем…" : "Изменить пароль"}</Button>
    </form>}
  </section>;
}

export function AccountSecurity({ email = "" }: { email?: string }) {
  const client = useQueryClient(); const router = useRouter();
  const sessions = useQuery({ queryKey: ["auth-sessions"], queryFn: listSessions });
  const revoke = useMutation({
    mutationFn: (item: AuthSession) => revokeSession(item.id), onSuccess: (_, item) => {
      if (item.current) router.replace("/login");
      else void client.invalidateQueries({ queryKey: ["auth-sessions"] });
    }
  });
  return <div className="account-security">
    <div className="space-y-5">
    <PasswordChangeForm key={email} email={email} />
    <section className="account-panel">
      <h2>Устройства и сессии</h2>
      <p className="account-hint">Завершите доступ на устройстве, которое не узнаёте.</p>
      <QueryError error={sessions.error} retry={() => void sessions.refetch()} />
      <QueryError error={revoke.error} />
      {sessions.isPending && <p role="status">Загружаем сессии…</p>}
      {sessions.data?.length === 0 && <p>Активные сессии не найдены.</p>}
      {sessions.data?.map(item => <div className={`account-session ${item.current ? "current" : ""}`} key={item.id}>
        <Laptop className="size-5" />
        <div>
          <h3>{device(item.user_agent)}</h3>
          {item.current && <span className="account-status success">Текущая сессия</span>}
          <p>{item.ip_address ?? "IP не определён"}</p>
          <p>Последняя активность: {date(item.last_seen_at)}</p>
        </div>
        <Button variant="outline" size="sm" disabled={revoke.isPending} onClick={() => revoke.mutate(item)}>{item.current ? "Выйти" : "Завершить"}</Button>
      </div>)}
    </section>
    </div>
    <aside className="account-security-note">
      <ShieldCheck className="size-6" />
      <h2>Контроль доступа</h2>
      <p>После завершения сессии на этом устройстве потребуется снова войти в аккаунт.</p>
      <p>Выход из текущей сессии вернёт вас на страницу входа.</p>
    </aside>
  </div>;
}
