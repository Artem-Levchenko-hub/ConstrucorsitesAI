"use client";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Download, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { deleteAccount, exportAccount } from "@/lib/api/account";
import { QueryError } from "./account-presentation";

export function AccountProfile({ email }: { email: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState("");
  const remove = useMutation({ mutationFn: deleteAccount, onSuccess: () => router.replace("/") });
  const download = useMutation({
    mutationFn: async () => {
      const data = await exportAccount();
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const anchor = document.createElement("a"); anchor.href = url;
      anchor.download = `max-studio-account-${new Date().toISOString().slice(0, 10)}.json`;
      anchor.click(); URL.revokeObjectURL(url);
    }
  });
  return <>
    <section className="account-profile">
      <div className="account-identity">
        <span className="account-avatar" aria-hidden>{email.slice(0, 1).toUpperCase()}</span>
        <h2>Ваш аккаунт</h2>
        <p>{email}</p>
        <span className="account-status pending">MAX Studio</span>
      </div>
      <div className="account-panel">
        <h2>Личные данные</h2>
        <label htmlFor="account-email">Email для входа</label>
        <Input id="account-email" value={email} readOnly />
        <p className="account-hint">Этот адрес используется для входа и уведомлений аккаунта.</p>
        <div className="account-action-row">
          <div>
            <h3>Экспорт данных</h3>
            <p>Профиль, согласия, проекты и операции в формате JSON.</p>
          </div>
          <Button variant="outline" disabled={download.isPending} onClick={() => download.mutate()}>
            <Download className="size-4" />{download.isPending ? "Подготовка…" : "Скачать данные"}</Button>
        </div>
        <QueryError error={download.error} />
      </div>
    </section>
    <section className="account-action-row account-danger">
      <div>
        <h3>Удаление аккаунта</h3>
        <p>Доступ и секреты будут отозваны сразу. Перед удалением скачайте данные.</p>
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
