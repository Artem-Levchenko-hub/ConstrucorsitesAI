"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Download, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { deleteAccount, exportAccount } from "@/lib/api/account";
import { QueryError } from "./account-presentation";
import { getAccountCredentials, requestAccountEmailChange } from "@/lib/api/account-credentials";

function EmailChangeForm({ email }: { email: string }) {
  const credentials = useQuery({ queryKey: ["account-credentials", email], queryFn: getAccountCredentials });
  const active = useRef(false);
  const submitting = useRef(false);
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    if (submitting.current || !form.reportValidity()) return;
    const values = new FormData(form);
    submitting.current = true; setPending(true); setError(null); setSent(false);
    try {
      await requestAccountEmailChange(String(values.get("current_password") ?? ""), String(values.get("new_email") ?? ""));
      if (!active.current) return;
      form.reset(); setSent(true);
    } catch (value) {
      if (active.current) setError(value instanceof Error ? value : new Error("Не удалось отправить подтверждение"));
    } finally {
      submitting.current = false;
      if (active.current) setPending(false);
    }
  }
  return <div className="mt-5 space-y-4">
    <h3>Изменить email для входа</h3>
    <QueryError error={credentials.error} retry={() => void credentials.refetch()} />
    {credentials.isPending && <p role="status">Проверяем способы входа…</p>}
    {credentials.data?.has_password === false && <p className="account-hint">Сначала <Link href="/forgot-password" className="underline">установите пароль через восстановление доступа</Link> для текущего email.</p>}
    {credentials.data?.has_password && <form onSubmit={submit} className="space-y-4">
      <p className="account-hint">Потребуется подтвердить старый и новый адрес. До этого email для входа останется прежним. После смены адреса нужно будет войти заново.</p>
      <div className="space-y-2"><label htmlFor="account-new-email">Новый email</label>
        <Input id="account-new-email" name="new_email" type="email" autoComplete="email" required maxLength={254} disabled={pending} /></div>
      <div className="space-y-2"><label htmlFor="account-email-password">Текущий пароль</label>
        <Input id="account-email-password" name="current_password" type="password" autoComplete="current-password" required maxLength={128} disabled={pending} /></div>
      <QueryError error={error} />
      {sent && <p role="status">Письма отправлены. Подтвердите оба адреса по ссылкам в письмах. Пока входите с прежним email.</p>}
      <Button type="submit" variant="outline" disabled={pending}>{pending ? "Отправляем…" : "Отправить подтверждения"}</Button>
    </form>}
  </div>;
}

export function AccountProfile({ email }: { email: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [exported, setExported] = useState(false);
  const download = useMutation({
    mutationFn: exportAccount,
    onMutate: () => setExported(false),
    onSuccess: (data) => {
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "yleum-account-data.json";
      document.body.append(link);
      link.click();
      link.remove();
      // Give the browser time to start consuming the download before revoking.
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setExported(true);
    },
  });
  const remove = useMutation({ mutationFn: deleteAccount, onSuccess: () => router.replace("/") });
  return <>
    <section className="account-profile">
      <div className="account-identity">
        <span className="account-avatar" aria-hidden>{email.slice(0, 1).toUpperCase()}</span>
        <h2>Ваш аккаунт</h2>
        <p>{email}</p>
        <span className="account-status pending">Yleum</span>
      </div>
      <div className="account-panel">
        <h2>Личные данные</h2>
        <label htmlFor="account-email">Email для входа</label>
        <Input id="account-email" value={email} readOnly />
        <p className="account-hint">Этот адрес используется для входа и уведомлений аккаунта.</p>
        <EmailChangeForm key={email} email={email} />
      </div>
    </section>
    <section className="account-action-row">
      <div>
        <h3>Выгрузка личных данных</h3>
        <p>Профиль, способы входа, согласия, сессии, сведения о ваших проектах и платежах в файле JSON.</p>
        <p className="account-hint">Исходники и данные пользователей созданных приложений в эту выгрузку не входят.</p>
        <QueryError error={download.error} />
        {exported && <p role="status">Файл личных данных подготовлен.</p>}
      </div>
      <Button variant="outline" disabled={download.isPending} onClick={() => download.mutate()}>
        <Download className="size-4" />{download.isPending ? "Подготовка…" : "Скачать личные данные"}
      </Button>
    </section>
    <section className="account-action-row account-danger">
      <div>
        <h3>Удаление аккаунта</h3>
        <p>Доступ и секреты будут отозваны сразу.</p>
      </div>
      <Button variant="outline" onClick={() => { setConfirm(""); setOpen(true); }}>
        <Trash2 className="size-4" />Удалить аккаунт</Button>
    </section>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent data-max-studio className="account-dialog">
        <DialogTitle>Удалить аккаунт?</DialogTitle>
        <DialogDescription>Доступ и секреты отзываются сразу. Операционные данные удаляются после 30-дневного защитного периода; обязательные платёжные документы сохраняются установленный законом срок.</DialogDescription>
        <label htmlFor="delete-email">Введите {email} для подтверждения</label>
        <Input id="delete-email" value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="off" />
        <QueryError error={remove.error} />
        <Button variant="destructive" disabled={!email || confirm !== email || remove.isPending} onClick={() => remove.mutate()}>{remove.isPending ? "Удаление…" : "Подтвердить удаление"}</Button>
      </DialogContent>
    </Dialog>
  </>;
}
