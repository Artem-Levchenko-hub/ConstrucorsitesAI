"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api/client";
import { getMaxAccess, registrationVerificationReason, requestPhoneVerification, verifyPhoneCode } from "@/lib/api/max-account";
import { EmailVerification } from "./EmailVerification";

export function PhoneVerification({ email, initialPhone = "", available, onVerified }: {
  email: string; initialPhone?: string; available: boolean; onVerified: () => Promise<unknown> | void;
}) {
  const [phone, setPhone] = useState(initialPhone);
  const [sentPhone, setSentPhone] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState<"send" | "verify" | null>(null);
  const busy = useRef(false);
  const [resendAt, setResendAt] = useState(0);
  const [remaining, setRemaining] = useState(0);

  useEffect(() => {
    if (!resendAt) return;
    const update = () => setRemaining(Math.max(0, Math.ceil((resendAt - Date.now()) / 1000)));
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [resendAt]);

  async function send() {
    if (busy.current || !available || Date.now() < resendAt) return;
    busy.current = true; setPending("send"); setError("");
    try {
      const result = await requestPhoneVerification(phone.trim());
      if (!result.accepted) throw new Error("Не удалось отправить SMS. Попробуйте ещё раз.");
      setSentPhone(phone.trim()); setCode("");
      const seconds = Math.max(60, result.resend_after || 60);
      setRemaining(seconds); setResendAt(Date.now() + seconds * 1000);
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "phone_rate_limited") {
        setRemaining(60); setResendAt(Date.now() + 60_000);
      }
      setError(cause instanceof Error ? cause.message : "Не удалось отправить SMS. Попробуйте ещё раз.");
    } finally { busy.current = false; setPending(null); }
  }

  async function verify() {
    if (busy.current) return;
    if (!/^\d{6}$/.test(code)) { setError("Введите 6 цифр из SMS"); return; }
    busy.current = true; setPending("verify"); setError("");
    try {
      const result = await verifyPhoneCode(code);
      if (!result.verified) throw new Error("Не удалось подтвердить код");
      await onVerified();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось подтвердить код");
    } finally { busy.current = false; setPending(null); }
  }

  return <section className="space-y-5" aria-labelledby="phone-verification-title">
    <div>
      <h2 id="phone-verification-title" className="text-2xl font-semibold">Подтвердите телефон</h2>
      <p className="mt-2 text-sm leading-6 text-fg-secondary">Это нужно один раз. Затем входите по email и паролю, без SMS. Email {email} сохранён для входа и восстановления пароля; подтверждать его необязательно.</p>
      <p className="mt-2 text-sm text-fg-tertiary">Регистрация бесплатна. Генерация расходует баланс, публикация зависит от тарифа.</p>
      <p className="mt-2 text-sm leading-6 text-fg-secondary">Для доставки кода передадим номер и SMS сервису Green SMS. <Link href="/legal/privacy" target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">Политика конфиденциальности</Link>.</p>
    </div>
    {!available && <p role="status" className="text-sm text-warning">SMS временно недоступны. Аккаунт сохранён. Попробуйте позже или обновите статус.</p>}
    <form data-phone-request className="space-y-3" onSubmit={event => { event.preventDefault(); void send(); }}>
      <Label htmlFor="verification-phone">Телефон с кодом страны</Label>
      <Input id="verification-phone" name="phone" type="tel" inputMode="tel" autoComplete="tel" required value={phone}
        disabled={pending !== null} onChange={event => { setPhone(event.target.value); setError(""); }} className="h-12" />
      <Button type="submit" variant="outline" disabled={pending !== null || !available || remaining > 0} className="min-h-12 w-full sm:w-auto">
        {pending === "send" && <Loader2 className="mr-2 size-4 animate-spin" />}
        {remaining > 0 ? `Повторить через ${remaining} с` : sentPhone ? "Отправить SMS ещё раз" : "Получить код по SMS"}
      </Button>
    </form>
    <p id="phone-code-help" role="status" className="text-sm text-fg-secondary">
      {sentPhone
        ? <>Запрос на SMS принят для {sentPhone}. SMS может прийти с задержкой. Код действует 10 минут.</>
        : "Если SMS уже пришло, введите код. Повторно отправлять SMS не нужно. Код действует 10 минут."}
    </p>
    {/* The server retains the session's challenge when the send response is lost. */}
    <form data-phone-verify className="space-y-3" onSubmit={event => { event.preventDefault(); void verify(); }}>
      <Label htmlFor="phone-verification-code">Код из SMS</Label>
      <Input id="phone-verification-code" name="one-time-code" type="text" inputMode="numeric" autoComplete="one-time-code"
        pattern="[0-9]{6}" maxLength={6} required value={code} disabled={pending !== null}
        onChange={event => { setCode(event.target.value.replace(/\D/g, "").slice(0, 6)); setError(""); }}
        aria-invalid={Boolean(error)} aria-describedby={error ? "phone-code-help phone-verification-error" : "phone-code-help"} className="h-12 text-lg tracking-[0.2em]" />
      <Button type="submit" disabled={pending !== null} className="min-h-12 w-full bg-accent text-fg-on-accent sm:w-auto">
        {pending === "verify" && <Loader2 className="mr-2 size-4 animate-spin" />}Подтвердить телефон
      </Button>
    </form>
    {error && <p id="phone-verification-error" role="alert" className="text-sm text-warning">{error}</p>}
  </section>;
}

/** Registration already established a durable session; SMS failures never replay registration. */
export function RegistrationPhoneConfirmation({ phone, email, next = "/max" }: { phone: string; email: string; next?: string }) {
  const router = useRouter();
  const access = useQuery({ queryKey: ["max-access", email], queryFn: getMaxAccess, staleTime: 0 });
  const verificationReason = access.data && registrationVerificationReason(access.data);
  const phoneRequired = verificationReason === "phone_verification_required";
  const emailRequired = verificationReason === "email_verification_required";
  const legalRequired = access.data?.reason === "legal_acceptance_required";
  useEffect(() => {
    if (legalRequired) {
      router.replace(`/max/onboarding?next=${encodeURIComponent(next)}`);
    } else if (access.data && !verificationReason) {
      router.replace(access.data.can_create_project ? next : `/max/onboarding?next=${encodeURIComponent(next)}`);
    }
  }, [access.data, legalRequired, verificationReason, next, router]);
  if (access.isPending) return <p role="status">Проверяем доступ…</p>;
  if (!access.data) return <div role="alert">Не удалось проверить доступ. Аккаунт сохранён.<Button type="button" onClick={() => access.refetch()}>Повторить</Button></div>;
  if (legalRequired) return <p role="status">Открываем действующие документы Yleum…</p>;
  if (emailRequired) return <EmailVerification email={email} available={access.data.email_delivery_configured} onVerified={() => access.refetch()} />;
  if (!phoneRequired) return <p role="status">Открываем Yleum…</p>;
  return <PhoneVerification email={email} initialPhone={phone} available={Boolean(access.data.phone_verification_available)} onVerified={() => access.refetch()} />;
}
