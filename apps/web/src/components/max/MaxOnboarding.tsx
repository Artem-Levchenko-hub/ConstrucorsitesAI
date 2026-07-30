"use client";

import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Building2,
  Check,
  CircleAlert,
  Loader2,
  MailCheck,
  RefreshCw,
  ShieldCheck,
  UserRoundCheck,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  getMaxAccess,
  resendVerification,
  saveBusinessProfile,
  type BusinessKind,
} from "@/lib/api/max-account";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

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
  const router = useRouter();
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<BusinessKind>("self_employed");
  const [inn, setInn] = useState("");
  const [ogrn, setOgrn] = useState("");
  const [legalName, setLegalName] = useState("");
  const access = useQuery({
    queryKey: ["max-access"],
    queryFn: getMaxAccess,
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (access.data?.can_create_project) {
      router.replace("/max");
    }
  }, [access.data?.can_create_project, router]);

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
          description: "Открываем создание приложения.",
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
        <Loader2 className="size-6 animate-spin text-[#7897f4]" />
      </div>
    );
  }

  const data = access.data;
  const business = data?.business;
  const step = !data?.email_verified ? 1 : business ? 3 : 2;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!save.isPending) save.mutate();
  }

  return (
    <main className="max-studio-scroll flex-1 overflow-y-auto bg-[#121310] px-5 py-10 text-white">
      <div className="mx-auto max-w-4xl">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[#7897f4]">
          Настройка владельца
        </p>
        <div className="mt-3 flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
          <div>
            <h1 className="text-3xl font-semibold tracking-[-0.03em] sm:text-4xl">
              Подготовим доступ к MAX Studio
            </h1>
            <p className="mt-3 text-sm text-white/45">
              Проверка выполняется один раз для всех будущих приложений бизнеса.
            </p>
          </div>
          <span className="text-sm text-white/35">Шаг {step} из 3</span>
        </div>

        <div className="mt-8 grid gap-3 sm:grid-cols-3">
          {[
            [MailCheck, "Email", data?.email_verified],
            [Building2, "Владелец", Boolean(business)],
            [ShieldCheck, "Проверка", business?.status === "verified"],
          ].map(([Icon, label, complete], index) => {
            const ItemIcon = Icon as typeof MailCheck;
            return (
              <div
                key={String(label)}
                className={cn(
                  "flex items-center gap-3 rounded-2xl border px-4 py-3 text-sm",
                  complete
                    ? "border-emerald-400/20 bg-emerald-400/[0.06] text-emerald-200"
                    : index + 1 === step
                      ? "border-[#7897f4]/45 bg-[#315bd7]/12"
                      : "border-white/[0.08] text-white/35",
                )}
              >
                {complete ? <Check className="size-4" /> : <ItemIcon className="size-4" />}
                {String(label)}
              </div>
            );
          })}
        </div>

        {!data?.email_verified && (
          <section className="mt-8 rounded-xl border border-white/10 bg-[#1a1b18] p-6 sm:p-8">
            <MailCheck className="size-6 text-[#7897f4]" />
            <h2 className="mt-5 text-2xl font-semibold">Подтвердите рабочий email</h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-white/50">
              Мы отправили ссылку на <span className="text-white">{email}</span>.
              После перехода вернитесь сюда — статус обновится автоматически.
            </p>
            {!data?.email_delivery_configured && (
              <div className="mt-5 flex gap-3 rounded-2xl border border-amber-400/20 bg-amber-400/[0.06] p-4 text-sm text-amber-100/80">
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
              className="mt-6 border-white/10 bg-transparent"
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
            className="mt-8 rounded-xl border border-white/10 bg-[#1a1b18] p-6 sm:p-8"
          >
            <UserRoundCheck className="size-6 text-[#7897f4]" />
            <h2 className="mt-5 text-2xl font-semibold">Кто владеет приложением?</h2>
            <p className="mt-2 text-sm text-white/45">
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
                    "rounded-2xl border p-4 text-left transition",
                    kind === item.id
                      ? "border-[#7897f4]/60 bg-[#315bd7]/14"
                      : "border-white/[0.08] hover:border-white/20",
                  )}
                >
                  <span className="block text-sm font-medium">{item.title}</span>
                  <span className="mt-1 block text-xs leading-5 text-white/35">
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
                  className="h-12 border-white/10 bg-black/20"
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
                  className="h-12 border-white/10 bg-black/20"
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
                    className="h-12 border-white/10 bg-black/20"
                  />
                </div>
              )}
            </div>

            <Button
              disabled={save.isPending}
              className="mt-7 h-12 rounded-md bg-[#315bd7] px-6 hover:bg-[#2449b7]"
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
          <section className="mt-8 rounded-xl border border-white/10 bg-[#1a1b18] p-6 sm:p-8">
            <div className="flex size-11 items-center justify-center rounded-2xl bg-amber-400/10 text-amber-300">
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
            <p className="mt-2 max-w-xl text-sm leading-6 text-white/50">
              {business.verification_note ??
                "Результат появится здесь. Повторно создавать аккаунт не нужно."}
            </p>
            <dl className="mt-6 grid gap-3 rounded-2xl border border-white/[0.08] p-4 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-white/35">Владелец</dt>
                <dd className="mt-1">{business.legal_name}</dd>
              </div>
              <div>
                <dt className="text-white/35">ИНН</dt>
                <dd className="mt-1 font-mono">{business.inn}</dd>
              </div>
            </dl>
            <Button
              variant="outline"
              onClick={() => access.refetch()}
              className="mt-6 border-white/10 bg-transparent"
            >
              <RefreshCw className="mr-2 size-4" />
              Обновить статус
            </Button>
          </section>
        )}
      </div>
    </main>
  );
}
