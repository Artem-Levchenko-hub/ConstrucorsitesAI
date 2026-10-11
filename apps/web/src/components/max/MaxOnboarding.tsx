"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  CircleAlert,
  Loader2,
  MailCheck,
  RefreshCw,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  getMaxAccess,
  resendVerification,
  verifyEmailCode,
} from "@/lib/api/max-account";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import "@/components/max/max-studio.css";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : "Не удалось выполнить действие";
}

export function MaxOnboarding({ email }: { email: string }) {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState("");
  const [letterSent, setLetterSent] = useState(false);
  const access = useQuery({
    queryKey: ["max-access"],
    queryFn: getMaxAccess,
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (access.data?.email_verified) {
      router.replace("/max");
    }
  }, [access.data?.email_verified, router]);

  const resend = useMutation({
    mutationFn: resendVerification,
    onSuccess: () => {
      setLetterSent(true);
      setCode("");
      setCodeError("");
      toast.success("Письмо отправлено", {
        description: "Проверьте входящие и папку «Спам».",
      });
    },
    onError: (error) =>
      toast.error("Не удалось отправить письмо", {
        description: errorMessage(error),
      }),
  });

  const verifyCode = useMutation({
    mutationFn: verifyEmailCode,
    onSuccess: async () => {
      setCodeError("");
      await access.refetch();
      router.replace("/max");
    },
    onError: (error) => setCodeError(errorMessage(error)),
  });

  if (access.isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Loader2 className="size-6 animate-spin text-accent" />
      </div>
    );
  }

  const data = access.data;
  const step = data?.email_verified ? 2 : 1;

  return (
    <main
      data-max-studio
      className="max-studio-scroll flex-1 overflow-y-auto bg-bg-base px-5 py-10 text-fg-primary"
    >
      <div className="mx-auto max-w-4xl">
        <p className="omnia-kicker text-accent">Настройка доступа</p>
        <div className="mt-3 flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
          <div>
            <h1 className="text-3xl font-semibold tracking-[-0.03em] sm:text-4xl">
              Подготовим доступ к Yleum
            </h1>
            <p className="mt-3 text-sm text-fg-secondary">
              Нужен только подтверждённый email. Остальное настроите уже внутри
              студии и MAX Partner.
            </p>
          </div>
          <span className="text-sm text-fg-tertiary">Шаг {step} из 2</span>
        </div>

        <div className="mt-8 grid gap-3 sm:grid-cols-2">
          {[
            [MailCheck, "Email", data?.email_verified],
            [ArrowRight, "Yleum", data?.email_verified],
          ].map(([Icon, label, complete], index) => {
            const ItemIcon = Icon as typeof MailCheck;
            return (
              <div
                key={String(label)}
                className={cn(
                  "flex items-center gap-3 rounded-[10px] border px-4 py-3 text-sm",
                  complete
                    ? "border-success/25 bg-success/[0.06] text-success-fg"
                    : index + 1 === step
                      ? "border-accent/45 bg-accent-subtle"
                      : "border-border-default text-fg-tertiary",
                )}
              >
                {complete ? (
                  <Check className="size-4" />
                ) : (
                  <ItemIcon className="size-4" />
                )}
                {String(label)}
              </div>
            );
          })}
        </div>

        {!data?.email_verified && (
          <section className="mt-8 rounded-[12px] border border-border-default bg-surface p-6 sm:p-8">
            <MailCheck className="size-6 text-accent" />
            <h2 className="mt-5 text-2xl font-semibold">
              Подтвердите рабочий email
            </h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-fg-secondary">
              {/* Пока отправка писем не настроена, обещать отправленную ссылку
                  нельзя: письма не уходит, и человек будет ждать его напрасно. */}
              {data?.email_delivery_configured ? (
                <>
                  {letterSent ? "Письмо отправлено на" : "Если письмо уже пришло на"}{" "}
                  <span className="text-fg-primary">{email}</span>, в нём есть код на 10 минут
                  и ссылка на 24 часа.
                </>
              ) : (
                <>
                  Адрес <span className="text-fg-primary">{email}</span> сохранён.
                  Отправка писем пока недоступна. Повторите отправку после подключения почты.
                </>
              )}
            </p>
            {!data?.email_delivery_configured && (
              <div className="mt-5 flex gap-3 rounded-[10px] border border-warning/40 bg-warning/10 p-4 text-sm text-warning">
                <CircleAlert className="mt-0.5 size-4 shrink-0" />
                Почтовый канал ещё подключается. Аккаунт сохранён.
              </div>
            )}
            {data?.email_delivery_configured && (
              <form
                className="mt-6 max-w-sm"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (verifyCode.isPending || resend.isPending) return;
                  setCodeError("");
                  if (/^\d{6}$/.test(code)) verifyCode.mutate(code);
                  else setCodeError("Введите 6 цифр из письма");
                }}
              >
                <label htmlFor="email-verification-code" className="block text-sm font-medium">
                  Код из письма
                </label>
                <input
                  id="email-verification-code"
                  name="one-time-code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  value={code}
                  onChange={(event) => {
                    setCode(event.target.value.replace(/\D/g, "").slice(0, 6));
                    setCodeError("");
                  }}
                  aria-invalid={Boolean(codeError)}
                  aria-describedby={codeError ? "email-code-error" : undefined}
                  className="mt-2 h-12 w-full rounded-lg border border-border-strong bg-surface px-4 text-lg tracking-[0.2em] text-fg-primary focus-visible:outline-2 focus-visible:outline-accent"
                />
                {codeError && <p id="email-code-error" role="alert" className="mt-2 text-sm text-warning">{codeError}</p>}
                <Button type="submit" disabled={verifyCode.isPending || resend.isPending} className="mt-3 h-11 bg-accent text-fg-on-accent">
                  {verifyCode.isPending && <Loader2 className="mr-2 size-4 animate-spin" />}
                  Подтвердить код
                </Button>
              </form>
            )}
            <Button
              type="button"
              variant="outline"
              disabled={resend.isPending || !data?.email_delivery_configured}
              onClick={() => resend.mutate()}
              className="mt-6 border-border-strong bg-surface"
            >
              {resend.isPending ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-2 size-4" />
              )}
              Отправить ещё раз
            </Button>
          </section>
        )}

        {data?.email_verified && (
          <section className="mt-8 rounded-[12px] border border-border-default bg-surface p-6 sm:p-8">
            <div className="flex size-11 items-center justify-center rounded-[10px] bg-success/10 text-success-fg">
              <Check className="size-5" />
            </div>
            <h2 className="mt-5 text-2xl font-semibold">Email подтверждён</h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-fg-secondary">
              Открываем Yleum. Реквизиты бизнеса не нужны вообще, а секрет
              бота — только перед публикацией: проект, первая генерация и превью
              доступны сразу.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <Button
                type="button"
                onClick={() => router.replace("/max")}
                className="h-12 rounded-lg bg-accent px-6 text-fg-on-accent hover:bg-accent-hover"
              >
                <ArrowRight className="mr-2 size-4" />
                Открыть Yleum
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => access.refetch()}
                className="h-12 border-border-strong bg-surface"
              >
                <RefreshCw className="mr-2 size-4" />
                Обновить статус
              </Button>
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
