"use client";

import { useEffect } from "react";
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
} from "@/lib/api/max-account";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : "Не удалось выполнить действие";
}

export function MaxOnboarding({ email }: { email: string }) {
  const router = useRouter();
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
    mutationFn: () => resendVerification(email),
    onSuccess: () =>
      toast.success("Письмо отправлено", {
        description: "Проверьте входящие и папку «Спам».",
      }),
    onError: (error) =>
      toast.error("Не удалось отправить письмо", {
        description: errorMessage(error),
      }),
  });

  if (access.isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Loader2 className="size-6 animate-spin text-[#4f81f7]" />
      </div>
    );
  }

  const data = access.data;
  const step = data?.email_verified ? 2 : 1;

  return (
    <main
      data-product-shell
      className="max-studio-scroll flex-1 overflow-y-auto bg-[#121519] px-5 py-10 text-white"
    >
      <div className="mx-auto max-w-4xl">
        <p className="omnia-kicker text-[#4f81f7]">Настройка владельца</p>
        <div className="mt-3 flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
          <div>
            <h1 className="text-3xl font-semibold tracking-[-0.03em] sm:text-4xl">
              Подготовим доступ к MAX Studio
            </h1>
            <p className="mt-3 text-sm text-[#9fa1b1]">
              Нужен только подтверждённый email. Остальное настроите уже внутри
              студии и MAX Partner.
            </p>
          </div>
          <span className="text-sm text-[#828491]">Шаг {step} из 2</span>
        </div>

        <div className="mt-8 grid gap-3 sm:grid-cols-2">
          {[
            [MailCheck, "Email", data?.email_verified],
            [ArrowRight, "MAX Studio", data?.email_verified],
          ].map(([Icon, label, complete], index) => {
            const ItemIcon = Icon as typeof MailCheck;
            return (
              <div
                key={String(label)}
                className={cn(
                  "flex items-center gap-3 rounded-[10px] border px-4 py-3 text-sm",
                  complete
                    ? "border-[#248a4b]/25 bg-[#248a4b]/[0.06] text-success-fg"
                    : index + 1 === step
                      ? "border-[#4f81f7]/45 bg-[#4f81f7]/8"
                      : "border-[#2b2d32] text-[#828491]",
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
          <section className="mt-8 rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-6 sm:p-8">
            <MailCheck className="size-6 text-[#4f81f7]" />
            <h2 className="mt-5 text-2xl font-semibold">
              Подтвердите рабочий email
            </h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-[#9fa1b1]">
              Мы отправили ссылку на <span className="text-white">{email}</span>.
              После перехода вернитесь сюда — статус обновится автоматически.
            </p>
            {!data?.email_delivery_configured && (
              <div className="mt-5 flex gap-3 rounded-[10px] border border-[#e8c547]/40 bg-[#e8c547]/10 p-4 text-sm text-[#e8c547]">
                <CircleAlert className="mt-0.5 size-4 shrink-0" />
                Почтовый канал ещё подключается. Аккаунт сохранён; поддержка
                подтвердит адрес после настройки отправки.
              </div>
            )}
            <Button
              type="button"
              variant="outline"
              disabled={resend.isPending || !data?.email_delivery_configured}
              onClick={() => resend.mutate()}
              className="mt-6 border-[#2b2d32] bg-transparent"
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
          <section className="mt-8 rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-6 sm:p-8">
            <div className="flex size-11 items-center justify-center rounded-[10px] bg-[#248a4b]/10 text-success-fg">
              <Check className="size-5" />
            </div>
            <h2 className="mt-5 text-2xl font-semibold">Email подтверждён</h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-[#9fa1b1]">
              Открываем MAX Studio. Реквизиты бизнеса и секрет бота не нужны для
              создания проекта, первой генерации и безопасного превью.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <Button
                type="button"
                onClick={() => router.replace("/max")}
                className="h-12 rounded-lg bg-[#4f81f7] px-6 text-[#121519] hover:bg-[#6a95fa]"
              >
                <ArrowRight className="mr-2 size-4" />
                Открыть MAX Studio
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => access.refetch()}
                className="h-12 border-[#2b2d32] bg-transparent"
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
