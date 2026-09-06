"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Laptop, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { listSessions, revokeSession, type AuthSession } from "@/lib/api/account";
import { date, QueryError } from "./account-presentation";

function device(value: string | null) {
  if (!value) return "Неизвестное устройство";
  if (/iPhone|iPad/i.test(value)) return "iPhone или iPad";
  if (/Android/i.test(value)) return "Android";
  if (/Macintosh/i.test(value)) return "Mac";
  if (/Windows/i.test(value)) return "Windows";
  return "Браузер";
}
export function AccountSecurity() {
  const client = useQueryClient(); const router = useRouter();
  const sessions = useQuery({ queryKey: ["auth-sessions"], queryFn: listSessions });
  const revoke = useMutation({
    mutationFn: (item: AuthSession) => revokeSession(item.id), onSuccess: (_, item) => {
      if (item.current) router.replace("/login");
      else void client.invalidateQueries({ queryKey: ["auth-sessions"] });
    }
  });
  return <div className="account-security">
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
    <aside className="account-security-note">
      <ShieldCheck className="size-6" />
      <h2>Контроль доступа</h2>
      <p>После завершения сессии на этом устройстве потребуется снова войти в аккаунт.</p>
      <p>Выход из текущей сессии вернёт вас на страницу входа.</p>
    </aside>
  </div>;
}
