"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Building2,
  CheckCircle2,
  Download,
  Laptop,
  Loader2,
  LogOut,
  ReceiptRussianRuble,
  Shield,
  Trash2,
} from "lucide-react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createPayment,
  deleteAccount,
  exportAccount,
  getPaymentConfig,
  listPayments,
  listSessions,
  revokeSession,
} from "@/lib/api/account";
import { getMaxAccess } from "@/lib/api/max-account";

function formatDevice(value: string | null): string {
  if (!value) return "Неизвестное устройство";
  if (/iPhone|iPad/i.test(value)) return "iPhone или iPad";
  if (/Android/i.test(value)) return "Android";
  if (/Macintosh/i.test(value)) return "Mac";
  if (/Windows/i.test(value)) return "Windows";
  return "Браузер";
}

export type AccountView =
  | "all"
  | "profile"
  | "organization"
  | "security"
  | "billing"
  | "transactions"
  | "plan"
  | "admin";

export function AccountControlCenter({
  email,
  view = "all",
}: {
  email: string;
  view?: AccountView;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const sessions = useQuery({ queryKey: ["auth-sessions"], queryFn: listSessions });
  const payments = useQuery({ queryKey: ["payments"], queryFn: listPayments });
  const paymentConfig = useQuery({
    queryKey: ["payment-config"],
    queryFn: getPaymentConfig,
  });
  const maxAccess = useQuery({ queryKey: ["max-access"], queryFn: getMaxAccess });

  const revoke = useMutation({
    mutationFn: revokeSession,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auth-sessions"] }),
  });
  const pay = useMutation({
    mutationFn: createPayment,
    onSuccess: (payment) => {
      if (payment.confirmation_url) window.location.assign(payment.confirmation_url);
    },
    onError: (error) =>
      toast.error("Не удалось начать оплату", {
        description: error instanceof Error ? error.message : "Попробуйте позже",
      }),
  });
  const remove = useMutation({
    mutationFn: deleteAccount,
    onSuccess: () => router.replace("/"),
  });

  async function downloadData() {
    try {
      const data = await exportAccount();
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `lead-generator-account-${new Date().toISOString().slice(0, 10)}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error("Не удалось подготовить выгрузку", {
        description: error instanceof Error ? error.message : "Попробуйте позже",
      });
    }
  }

  return (
    <div className="space-y-6">
      {(view === "all" || view === "organization") && <section className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6">
        <div className="flex items-start gap-4">
          <div className="rounded-xl bg-accent/10 p-3 text-accent">
            <Building2 className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="font-medium">Владелец MAX-приложений</h2>
            {maxAccess.data?.business ? (
              <>
                <p className="mt-1 text-sm text-fg-secondary">
                  {maxAccess.data.business.legal_name} · ИНН{" "}
                  {maxAccess.data.business.inn}
                </p>
                <div className="mt-3 inline-flex items-center gap-2 rounded-full bg-success/10 px-3 py-1 text-xs text-success">
                  <CheckCircle2 className="size-3.5" />
                  {maxAccess.data.business.status === "verified"
                    ? "Проверен"
                    : "Проверяется"}
                </div>
              </>
            ) : (
              <p className="mt-1 text-sm text-fg-tertiary">
                Профиль появится после онбординга MAX Studio.
              </p>
            )}
          </div>
          <Button variant="outline" onClick={() => router.push("/max/onboarding")}>
            Настроить
          </Button>
        </div>
      </section>}

      {(view === "all" || view === "billing" || view === "transactions" || view === "plan") && <section className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6">
        <div className="flex items-start gap-4">
          <div className="rounded-xl bg-accent/10 p-3 text-accent">
            <ReceiptRussianRuble className="size-5" />
          </div>
          <div>
            <h2 className="font-medium">Пополнение</h2>
            <p className="mt-1 text-sm text-fg-tertiary">
              Оплата открывается на защищённой странице ЮKassa.
            </p>
          </div>
        </div>
        {view !== "transactions" && <div className="mt-5 grid gap-3 sm:grid-cols-3">
          {(paymentConfig.data?.packages ?? []).map((item) => (
            <button
              key={item.code}
              type="button"
              disabled={!paymentConfig.data?.enabled || pay.isPending}
              onClick={() => pay.mutate(item.code)}
              className="rounded-[10px] border border-[#d8d4cb] bg-white p-4 text-left transition hover:border-[#f15a38] disabled:cursor-not-allowed disabled:opacity-45"
            >
              <span className="block text-sm font-medium">{item.title}</span>
              <span className="mt-2 block text-lg font-semibold">
                {item.price_rub} ₽
              </span>
              <span className="mt-1 block text-xs text-fg-tertiary">
                Зачисление {item.credit_rub} ₽
              </span>
            </button>
          ))}
        </div>}
        {view !== "transactions" && (
          <p className="mt-3 text-xs leading-5 text-fg-tertiary">
            Нажимая на пакет и оплачивая его, вы принимаете{" "}
            <Link className="font-medium text-accent hover:underline" href="/legal/offer">
              публичную оферту
            </Link>
            . Это разовое пополнение, не подписка.
          </p>
        )}
        {!paymentConfig.data?.enabled && (
          <p className="mt-4 rounded-xl bg-warning/10 px-4 py-3 text-sm text-warning">
            {paymentConfig.data?.reason ?? "Платёжный контур пока недоступен"}
          </p>
        )}
        {payments.data && payments.data.length > 0 && (
          <div className="mt-5 space-y-2 border-t border-separator pt-4">
            {payments.data.slice(0, 5).map((payment) => (
              <div key={payment.id} className="flex justify-between text-sm">
                <span className="text-fg-secondary">
                  {new Date(payment.created_at).toLocaleDateString("ru-RU")}
                </span>
                <span>{payment.amount_rub} ₽ · {payment.status}</span>
              </div>
            ))}
          </div>
        )}
      </section>}

      {(view === "all" || view === "security") && <section className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6">
        <div className="flex items-start gap-4">
          <div className="rounded-xl bg-accent/10 p-3 text-accent">
            <Shield className="size-5" />
          </div>
          <div>
            <h2 className="font-medium">Активные сессии</h2>
            <p className="mt-1 text-sm text-fg-tertiary">
              Завершите доступ на устройстве, которое не узнаёте.
            </p>
          </div>
        </div>
        <div className="mt-5 divide-y divide-separator">
          {(sessions.data ?? []).map((item) => (
            <div key={item.id} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
              <Laptop className="size-4 text-fg-tertiary" />
              <div className="min-w-0 flex-1">
                <p className="text-sm">
                  {formatDevice(item.user_agent)}
                  {item.current && (
                    <span className="ml-2 text-xs text-success">текущая</span>
                  )}
                </p>
                <p className="text-xs text-fg-tertiary">
                  {item.ip_address ?? "IP не определён"}
                </p>
              </div>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={revoke.isPending}
                onClick={() => revoke.mutate(item.id)}
              >
                <LogOut className="mr-2 size-4" />
                Завершить
              </Button>
            </div>
          ))}
        </div>
      </section>}

      {(view === "all" || view === "profile") && <section className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-6">
        <div className="mb-6 grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-2 block text-xs font-medium text-[#6d6962]" htmlFor="account-email">Рабочий email</label>
            <Input id="account-email" value={email} readOnly className="border-[#d8d4cb] bg-[#f5f3ee]" />
          </div>
          <div>
            <label className="mb-2 block text-xs font-medium text-[#6d6962]" htmlFor="account-role">Роль</label>
            <Input id="account-role" value="Владелец бизнеса" readOnly className="border-[#d8d4cb] bg-[#f5f3ee]" />
          </div>
        </div>
        <h2 className="font-medium">Данные аккаунта</h2>
        <p className="mt-1 text-sm text-fg-tertiary">
          Скачайте машиночитаемую копию профиля, согласий, проектов и операций.
        </p>
        <Button variant="outline" className="mt-4" onClick={downloadData}>
          <Download className="mr-2 size-4" />
          Скачать данные
        </Button>

        <div className="mt-6 border-t border-danger/20 pt-6">
          <h3 className="text-sm font-medium text-danger">Удаление аккаунта</h3>
          <p className="mt-1 text-xs leading-5 text-fg-tertiary">
            Доступ и секреты отзываются сразу. Операционные данные удаляются после
            30-дневного защитного периода; обязательные платёжные документы
            сохраняются установленный законом срок.
          </p>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row">
            <Input
              value={deleteConfirm}
              onChange={(event) => setDeleteConfirm(event.target.value)}
              placeholder={`Введите ${email}`}
            />
            <Button
              variant="destructive"
              disabled={deleteConfirm !== email || remove.isPending}
              onClick={() => remove.mutate()}
            >
              {remove.isPending ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : (
                <Trash2 className="mr-2 size-4" />
              )}
              Удалить
            </Button>
          </div>
        </div>
      </section>}
    </div>
  );
}
