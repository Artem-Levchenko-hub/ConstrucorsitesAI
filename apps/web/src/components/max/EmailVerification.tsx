"use client";

import { useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { resendVerification, verifyEmailCode } from "@/lib/api/max-account";

/** Retain existing email proof admission until the SMS rollout is activated. */
export function EmailVerification({ email, available, onVerified }: {
  email: string; available: boolean; onVerified: () => Promise<unknown> | void;
}) {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  const [pending, setPending] = useState<"send" | "verify" | null>(null);
  const busy = useRef(false);
  async function send() {
    if (busy.current || !available) return;
    busy.current = true; setPending("send"); setError("");
    try {
      const result = await resendVerification();
      if (!result.accepted) throw new Error("Не удалось отправить письмо");
      setSent(true); setCode("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось отправить письмо"); }
    finally { busy.current = false; setPending(null); }
  }
  async function verify() {
    if (busy.current) return;
    if (!/^\d{6}$/.test(code)) { setError("Введите 6 цифр из письма"); return; }
    busy.current = true; setPending("verify"); setError("");
    try {
      const result = await verifyEmailCode(code);
      if (!result.verified) throw new Error("Не удалось подтвердить код");
      await onVerified();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось подтвердить код"); }
    finally { busy.current = false; setPending(null); }
  }
  return <section className="space-y-5" aria-labelledby="email-verification-title">
    <h2 id="email-verification-title" className="text-2xl font-semibold">Подтвердите email</h2>
    {available ? <>
      <p className="text-sm leading-6 text-fg-secondary">{sent ? "Письмо отправлено на" : "Если письмо уже пришло на"} {email}, в нём есть код на 10 минут и ссылка на 24 часа.</p>
      <form data-email-verify className="space-y-3" onSubmit={event => { event.preventDefault(); void verify(); }}>
        <Label htmlFor="email-verification-code">Код из письма</Label>
        <Input id="email-verification-code" type="text" name="one-time-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required
          value={code} disabled={pending !== null} onChange={event => { setCode(event.target.value.replace(/\D/g, "").slice(0, 6)); setError(""); }}
          aria-invalid={Boolean(error)} aria-describedby={error ? "email-verification-error" : undefined} className="h-12 text-lg tracking-[0.2em]" />
        <Button type="submit" disabled={pending !== null} className="min-h-12 bg-accent text-fg-on-accent">
          {pending === "verify" && <Loader2 className="mr-2 size-4 animate-spin" />}Подтвердить код
        </Button>
      </form>
    </> : <p role="status" className="text-sm leading-6 text-warning">Адрес {email} сохранён. Отправка писем пока недоступна. Аккаунт сохранён; попробуйте позже.</p>}
    <Button type="button" variant="outline" disabled={pending !== null || !available} onClick={() => void send()} className="min-h-11">
      {pending === "send" && <Loader2 className="mr-2 size-4 animate-spin" />}Отправить ещё раз
    </Button>
    {error && <p id="email-verification-error" role="alert" className="text-sm text-warning">{error}</p>}
  </section>;
}
