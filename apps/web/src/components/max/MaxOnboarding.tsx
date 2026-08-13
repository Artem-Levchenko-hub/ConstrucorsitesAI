"use client";

import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Building2,
  Check,
  CircleAlert,
  CreditCard,
  Eye,
  Images,
  Loader2,
  MailCheck,
  RefreshCw,
  ShieldCheck,
  UserRoundCheck,
} from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { MaxHowToDialog } from "@/components/max/MaxHowToDialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  getMaxAccess,
  resendVerification,
  saveBusinessProfile,
  type BusinessKind,
} from "@/lib/api/max-account";
import { ApiError } from "@/lib/api/client";
import {
  useMaxDemoDraft,
} from "@/hooks/useMaxDemoDraft";
import { cn } from "@/lib/utils";
import { getMaxHowToGuide } from "@/lib/max-how-to";

const kinds: Array<{
  id: BusinessKind;
  title: string;
  hint: string;
}> = [
  { id: "legal_entity", title: "Организация", hint: "ООО, АО и другие юрлица" },
  { id: "sole_proprietor", title: "ИП", hint: "Индивидуальный предприниматель" },
  { id: "self_employed", title: "Самозанятый", hint: "Плательщик НПД" },
];

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : "Не удалось выполнить действие";
}

export function MaxOnboarding({ email }: { email: string }) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<BusinessKind>("self_employed");
  const [inn, setInn] = useState("");
  const [ogrn, setOgrn] = useState("");
  const [legalName, setLegalName] = useState("");
  const demoDraft = useMaxDemoDraft();
  const access = useQuery({
    queryKey: ["max-access"],
    queryFn: getMaxAccess,
    refetchInterval: 15_000,
  });

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

  const save = useMutation({
    mutationFn: () =>
      saveBusinessProfile({
        kind,
        inn: inn.replace(/\s/g, ""),
        ogrn: kind === "self_employed" ? undefined : ogrn.replace(/\s/g, ""),
        legal_name: legalName.trim(),
      }),
    onSuccess: (profile) => {
      queryClient.invalidateQueries({ queryKey: ["max-access"] });
      if (profile.status === "verified") {
        toast.success("Самозанятость подтверждена", {
          description: "Теперь можно вернуться к проекту и перейти к публикации.",
        });
      } else {
        toast.success("Реквизиты сохранены", {
          description: "Мы покажем результат проверки на этой странице.",
        });
      }
    },
    onError: (error) =>
      toast.error("Не удалось сохранить реквизиты", {
        description: errorMessage(error),
      }),
  });

  if (access.isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <Loader2 className="size-6 animate-spin text-accent" />
      </div>
    );
  }

  const data = access.data;
  const business = data?.business;
  const step = !data?.email_verified
    ? 1
    : !business
      ? 2
      : business.status !== "verified"
        ? 3
        : 4;
  const ownerGuide = getMaxHowToGuide("access");

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!save.isPending) save.mutate();
  }

  return (
    <main data-light-shell className="max-studio-scroll flex-1 overflow-y-auto bg-[#f5f3ee] px-5 py-10 text-[#171716]">
      <div className="mx-auto max-w-4xl">
        <p className="omnia-kicker text-accent">
          Настройка владельца
        </p>
        <div className="mt-3 flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
          <div>
            <h1 className="text-3xl font-semibold tracking-[-0.03em] sm:text-4xl">
              Подготовим запуск в MAX
            </h1>
            <p className="mt-3 text-sm text-[#6d6962]">
              Проверка выполняется один раз для всех будущих приложений бизнеса.
            </p>
          </div>
          <span className="text-sm text-[#8d887f]">Шаг {step} из 4</span>
        </div>

        {demoDraft && (
          <section className="mt-6 flex flex-col gap-4 rounded-[12px] border border-accent/30 bg-accent-subtle p-5 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-semibold">Ваше демо «{demoDraft.brief.name}» сохранено</p>
              <p className="mt-1 text-xs leading-5 text-[#6d6962]">Пока идёт проверка, можно вернуться в интерактивный просмотр. Описание не потеряется.</p>
            </div>
            <Link href="/max/demo" className="omnia-button omnia-button-secondary min-h-10 shrink-0 px-4">
              <Eye className="size-4" /> Открыть демо
            </Link>
          </section>
        )}

        <div className="mt-8 grid gap-3 sm:grid-cols-4">
          {[
            [MailCheck, "Email", data?.email_verified],
            [Building2, "Владелец", Boolean(business)],
            [ShieldCheck, "Проверка", business?.status === "verified"],
            [CreditCard, "Публикация", data?.can_launch],
          ].map(([Icon, label, complete], index) => {
            const ItemIcon = Icon as typeof MailCheck;
            return (
              <div
                key={String(label)}
                className={cn(
                  "flex items-center gap-3 rounded-[10px] border px-4 py-3 text-sm",
                  complete
                    ? "border-[#248a4b]/25 bg-[#248a4b]/[0.06] text-[#248a4b]"
                    : index + 1 === step
                      ? "border-accent/45 bg-accent/8"
                      : "border-[#d8d4cb] text-[#8d887f]",
                )}
              >
                {complete ? <Check className="size-4" /> : <ItemIcon className="size-4" />}
                {String(label)}
              </div>
            );
          })}
        </div>

        <section
          data-testid="max-onboarding-how-to"
          className="mt-5 flex flex-col gap-4 rounded-[14px] border border-accent/35 bg-[#fcfbf7] p-5 shadow-[0_12px_36px_var(--color-accent-subtle)] sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="flex items-center gap-3">
            <span className="grid size-11 shrink-0 place-items-center rounded-[12px] bg-accent text-accent-fg">
              <Images className="size-5" />
            </span>
            <div>
              <p className="text-sm font-semibold">Не уверены, что заполнять?</p>
              <p className="mt-1 text-xs leading-5 text-[#6d6962]">
                Покажем весь путь владельца на изображении — от email до проверки реквизитов.
              </p>
            </div>
          </div>
          <MaxHowToDialog guide={ownerGuide} triggerClassName="w-full shrink-0 sm:w-auto" />
        </section>

        {!data?.email_verified && (
          <section className="mt-8 rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6 sm:p-8">
            <MailCheck className="size-6 text-accent" />
            <h2 className="mt-5 text-2xl font-semibold">Подтвердите рабочий email</h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-[#6d6962]">
              Мы отправили ссылку на <span className="text-[#171716]">{email}</span>.
              После перехода вернитесь сюда — статус обновится автоматически.
            </p>
            {!data?.email_delivery_configured && (
              <div className="mt-5 flex gap-3 rounded-[10px] border border-[#e8c547]/40 bg-[#e8c547]/10 p-4 text-sm text-[#745f16]">
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
              className="mt-6 border-[#d8d4cb] bg-transparent"
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

        {data?.email_verified && !business && (
          <form
            onSubmit={submit}
            className="mt-8 rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6 sm:p-8"
          >
            <UserRoundCheck className="size-6 text-accent" />
            <h2 className="mt-5 text-2xl font-semibold">Кто владеет приложением?</h2>
            <p className="mt-2 text-sm text-[#6d6962]">
              Тип должен совпадать с владельцем MAX-бота.
            </p>

            <div className="mt-6 grid gap-3 sm:grid-cols-3">
              {kinds.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    setKind(item.id);
                    setOgrn("");
                  }}
                  className={cn(
                    "rounded-[10px] border p-4 text-left transition",
                    kind === item.id
                      ? "border-accent/60 bg-accent/8"
                      : "border-[#d8d4cb] hover:border-[#aaa59b]",
                  )}
                >
                  <span className="block text-sm font-medium">{item.title}</span>
                  <span className="mt-1 block text-xs leading-5 text-[#8d887f]">
                    {item.hint}
                  </span>
                </button>
              ))}
            </div>

            <div className="mt-6 grid gap-5 sm:grid-cols-2">
              <div className="space-y-2 sm:col-span-2">
                <Label htmlFor="legal-name">
                  {kind === "legal_entity"
                    ? "Название организации"
                    : kind === "sole_proprietor"
                      ? "ФИО предпринимателя"
                      : "ФИО самозанятого"}
                </Label>
                <Input
                  id="legal-name"
                  value={legalName}
                  onChange={(event) => setLegalName(event.target.value)}
                  required
                  minLength={3}
                  maxLength={300}
                  className="h-12 border-[#d8d4cb] bg-white"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="business-inn">ИНН</Label>
                <Input
                  id="business-inn"
                  value={inn}
                  onChange={(event) => setInn(event.target.value.replace(/\D/g, ""))}
                  inputMode="numeric"
                  required
                  minLength={kind === "legal_entity" ? 10 : 12}
                  maxLength={kind === "legal_entity" ? 10 : 12}
                  className="h-12 border-[#d8d4cb] bg-white"
                />
              </div>
              {kind !== "self_employed" && (
                <div className="space-y-2">
                  <Label htmlFor="business-ogrn">
                    {kind === "legal_entity" ? "ОГРН" : "ОГРНИП"}
                  </Label>
                  <Input
                    id="business-ogrn"
                    value={ogrn}
                    onChange={(event) => setOgrn(event.target.value.replace(/\D/g, ""))}
                    inputMode="numeric"
                    required
                    minLength={kind === "legal_entity" ? 13 : 15}
                    maxLength={kind === "legal_entity" ? 13 : 15}
                    className="h-12 border-[#d8d4cb] bg-white"
                  />
                </div>
              )}
            </div>

            <Button
              disabled={save.isPending}
              className="mt-7 h-12 rounded-lg bg-accent px-6 text-white hover:bg-accent-hover"
            >
              {save.isPending ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : (
                <ArrowRight className="mr-2 size-4" />
              )}
              Проверить реквизиты
            </Button>
          </form>
        )}

        {business && business.status !== "verified" && (
          <section className="mt-8 rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6 sm:p-8">
            <div className="flex size-11 items-center justify-center rounded-[10px] bg-[#e8c547]/15 text-[#745f16]">
              {business.status === "rejected" ? (
                <CircleAlert className="size-5" />
              ) : (
                <Loader2 className="size-5 animate-spin" />
              )}
            </div>
            <h2 className="mt-5 text-2xl font-semibold">
              {business.status === "rejected"
                ? "Нужна корректировка реквизитов"
                : "Проверяем владельца"}
            </h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-[#6d6962]">
              {business.verification_note ??
                "Результат появится здесь. Повторно создавать аккаунт не нужно."}
            </p>
            <dl className="mt-6 grid gap-3 rounded-[10px] border border-[#d8d4cb] p-4 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-[#8d887f]">Владелец</dt>
                <dd className="mt-1">{business.legal_name}</dd>
              </div>
              <div>
                <dt className="text-[#8d887f]">ИНН</dt>
                <dd className="mt-1 font-mono">{business.inn}</dd>
              </div>
            </dl>
            <Button
              variant="outline"
              onClick={() => access.refetch()}
              className="mt-6 border-[#d8d4cb] bg-transparent"
            >
              <RefreshCw className="mr-2 size-4" />
              Обновить статус
            </Button>
          </section>
        )}

        {business?.status === "verified" && (
          <section className="mt-8 rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6 sm:p-8">
            <ShieldCheck className="size-7 text-[#248a4b]" />
            <h2 className="mt-5 text-2xl font-semibold">
              {data?.can_launch ? "Доступ к запуску готов" : "Осталось подключить публикацию"}
            </h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-[#6d6962]">
              {data?.can_launch
                ? "Бизнес подтверждён, а тариф включает постоянный HTTPS-адрес. Вернитесь к проекту и продолжите мастер запуска."
                : "Демо и превью остаются бесплатными. Pro нужен только для постоянного HTTPS-адреса, webhook и запуска приложения в MAX."}
            </p>
            <Button asChild className="mt-6 h-11">
              <Link href={data?.can_launch ? "/max" : "/billing/plan"}>
                {data?.can_launch ? "Вернуться к проектам" : "Подключить Pro"}
                <ArrowRight className="ml-2 size-4" />
              </Link>
            </Button>
          </section>
        )}
      </div>
    </main>
  );
}
