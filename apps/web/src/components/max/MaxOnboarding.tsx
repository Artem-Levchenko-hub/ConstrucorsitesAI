"use client";

import { useEffect } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  Loader2,
  Phone,
  RefreshCw,
} from "lucide-react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  acceptMaxDocuments,
  getMaxAccess,
  registrationVerificationReason,
} from "@/lib/api/max-account";
import { PhoneVerification } from "./PhoneVerification";
import { EmailVerification } from "./EmailVerification";
import { cn } from "@/lib/utils";
import "@/components/max/max-studio.css";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Не удалось выполнить действие";
}

export function MaxOnboarding({ email, next = "/max" }: { email: string; next?: string }) {
  const router = useRouter();
  const access = useQuery({
    queryKey: ["max-access", email],
    queryFn: getMaxAccess,
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (access.data?.can_create_project) {
      router.replace(next);
    }
  }, [access.data?.can_create_project, next, router]);

  const acceptDocuments = useMutation({
    mutationFn: acceptMaxDocuments,
    onSuccess: () => access.refetch(),
    onError: () => access.refetch(),
  });

  if (access.isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Loader2 className="size-6 animate-spin text-accent" />
      </div>
    );
  }

  const data = access.data;
  const verificationReason = data && registrationVerificationReason(data);
  const phoneRequired = verificationReason === "phone_verification_required";
  const emailRequired = verificationReason === "email_verification_required";
  const legalRequired = data?.reason === "legal_acceptance_required";
  const step = legalRequired ? 1 : 2;

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
              Примите документы Yleum{verificationReason ? emailRequired ? " и подтвердите email" : " и подтвердите телефон один раз" : ""}. Остальное настроите
              уже внутри студии и MAX Partner.
            </p>
          </div>
          <span className="text-sm text-fg-tertiary">Шаг {step} из 2</span>
        </div>

        <div className="mt-8 grid gap-3 sm:grid-cols-2">
          {[
            [ArrowRight, "Документы", !legalRequired],
            [Phone, phoneRequired || data?.phone_verified ? "Телефон" : "Email", !verificationReason],
          ].map(([Icon, label, complete], index) => {
            const ItemIcon = Icon as typeof Phone;
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

        {phoneRequired && !legalRequired && (
          <div className="mt-8 rounded-[12px] border border-border-default bg-surface p-6 sm:p-8">
            <PhoneVerification email={email} available={Boolean(data?.phone_verification_available)} onVerified={() => access.refetch()} />
            <Button type="button" variant="outline" className="mt-5 min-h-11" onClick={() => access.refetch()}>Обновить статус</Button>
          </div>
        )}
        {emailRequired && !legalRequired && (
          <div className="mt-8 rounded-[12px] border border-border-default bg-surface p-6 sm:p-8">
            <EmailVerification email={email} available={Boolean(data?.email_delivery_configured)} onVerified={() => access.refetch()} />
          </div>
        )}
        {legalRequired && data && (
          <section className="mt-8 rounded-[12px] border border-border-default bg-surface p-6 sm:p-8">
            <h2 className="text-2xl font-semibold">Документы Yleum</h2>
            <p className="mt-2 text-sm leading-6 text-fg-secondary">
              Перед созданием приложения примите действующие документы.
            </p>
            <form
              key={data.legal_document_version}
              className="mt-6 space-y-4 text-sm"
              onSubmit={(event) => {
                event.preventDefault();
                if (acceptDocuments.isPending || !event.currentTarget.reportValidity()) return;
                const form = new FormData(event.currentTarget);
                acceptDocuments.mutate({
                  terms_accepted: form.get("terms_accepted") === "on",
                  privacy_accepted: form.get("privacy_accepted") === "on",
                  personal_data_accepted: form.get("personal_data_accepted") === "on",
                  document_version: data.legal_document_version,
                });
              }}
            >
              {[
                ["terms_accepted", "/legal/terms", "Принимаю условия использования"],
                ["privacy_accepted", "/legal/privacy", "Ознакомлен с политикой конфиденциальности"],
                ["personal_data_accepted", "/legal/personal-data", "Даю согласие на обработку персональных данных"],
              ].map(([name, href, label]) => (
                <label key={name} className="flex cursor-pointer items-start gap-3">
                  <input name={name} type="checkbox" required className="mt-1 size-4 accent-[#0062ee]" />
                  <Link href={href} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
                    {label}
                  </Link>
                </label>
              ))}
              {acceptDocuments.error && (
                <p role="alert" className="text-warning">{errorMessage(acceptDocuments.error)}</p>
              )}
              <Button type="submit" disabled={acceptDocuments.isPending} className="h-12 bg-accent text-fg-on-accent">
                {acceptDocuments.isPending && <Loader2 className="mr-2 size-4 animate-spin" />}
                Принять документы и продолжить
              </Button>
            </form>
          </section>
        )}

        {data?.can_create_project && (
          <section className="mt-8 rounded-[12px] border border-border-default bg-surface p-6 sm:p-8">
            <div className="flex size-11 items-center justify-center rounded-[10px] bg-success/10 text-success-fg">
              <Check className="size-5" />
            </div>
            <h2 className="mt-5 text-2xl font-semibold">Доступ открыт</h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-fg-secondary">
              Открываем Yleum. Реквизиты бизнеса не нужны вообще, а секрет
              бота — только перед публикацией: проект, первая генерация и превью
              доступны сразу.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <Button
                type="button"
                onClick={() => router.replace(next)}
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
