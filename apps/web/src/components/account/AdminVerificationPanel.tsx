"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Building2,
  Check,
  CircleAlert,
  Loader2,
  Search,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  decideBusiness,
  listBusinessReviews,
  type BusinessReview,
} from "@/lib/api/max-account";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

const kindLabels: Record<BusinessReview["kind"], string> = {
  legal_entity: "Юридическое лицо",
  sole_proprietor: "Индивидуальный предприниматель",
  self_employed: "Самозанятый",
};

const statusLabels: Record<BusinessReview["status"], string> = {
  pending: "Ожидает проверки",
  verified: "Подтверждён",
  rejected: "Отклонён",
  suspended: "Приостановлен",
};

export function AdminVerificationPanel() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const [search, setSearch] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const reviews = useQuery({
    queryKey: ["admin-business-reviews"],
    queryFn: listBusinessReviews,
    retry: false,
  });
  const decision = useMutation({
    mutationFn: ({
      inn,
      approved,
      note,
    }: {
      inn: string;
      approved: boolean;
      note?: string;
    }) => decideBusiness(inn, approved, note),
    onSuccess: (profile) => {
      void queryClient.invalidateQueries({
        queryKey: ["admin-business-reviews"],
      });
      toast.success(
        profile.status === "verified"
          ? "Организация подтверждена"
          : "Заявка отклонена",
      );
    },
    onError: (error) =>
      toast.error("Не удалось сохранить решение", {
        description:
          error instanceof Error ? error.message : "Повторите попытку",
      }),
  });

  const visible = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("ru-RU");
    return (reviews.data ?? []).filter(
      (item) =>
        (filter === "all" || item.status === "pending") &&
        (!needle ||
          item.legal_name.toLocaleLowerCase("ru-RU").includes(needle) ||
          item.inn.includes(needle) ||
          item.owner_email.toLocaleLowerCase("ru-RU").includes(needle)),
    );
  }, [filter, reviews.data, search]);

  if (
    reviews.isError &&
    reviews.error instanceof ApiError &&
    reviews.error.status === 403
  ) {
    return (
      <section className="rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-8 text-center">
        <CircleAlert className="mx-auto size-7 text-danger-fg" />
        <h2 className="mt-4 text-lg font-semibold">Нет административного доступа</h2>
        <p className="mt-2 text-sm text-[#9fa1b1]">
          Этот аккаунт не имеет роли администратора.
        </p>
      </section>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex gap-2">
          {(["pending", "all"] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setFilter(value)}
              className={cn(
                "h-9 rounded-[8px] border px-3 text-xs",
                filter === value
                  ? "border-[#25272b] bg-[#121519] text-white"
                  : "border-[#2b2d32] text-[#9fa1b1]",
              )}
            >
              {value === "pending" ? "Ожидают" : "Все заявки"}
            </button>
          ))}
        </div>
        <label className="relative block w-full sm:w-[300px]">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[#828491]" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Название, ИНН или email"
            className="border-[#2b2d32] bg-[#191b20] pl-9"
          />
        </label>
      </div>

      {reviews.isLoading ? (
        <div className="grid min-h-[260px] place-items-center">
          <Loader2 className="size-6 animate-spin text-[#4f81f7]" />
        </div>
      ) : reviews.isError ? (
        <section className="rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-8 text-center">
          <CircleAlert className="mx-auto size-7 text-danger-fg" />
          <h2 className="mt-4 text-lg font-semibold">Не удалось загрузить очередь</h2>
          <p className="mt-2 text-sm text-[#9fa1b1]">
            {reviews.error instanceof Error
              ? reviews.error.message
              : "Повторите попытку"}
          </p>
          <Button variant="outline" className="mt-5" onClick={() => void reviews.refetch()}>
            Повторить
          </Button>
        </section>
      ) : visible.length === 0 ? (
        <section className="rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-10 text-center">
          <Check className="mx-auto size-7 text-success-fg" />
          <h2 className="mt-4 text-lg font-semibold">Очередь пуста</h2>
          <p className="mt-2 text-sm text-[#9fa1b1]">
            Новых заявок на ручную проверку сейчас нет.
          </p>
        </section>
      ) : (
        <div className="space-y-4">
          {visible.map((item) => (
            <article
              key={item.id}
              className="rounded-[12px] border border-[#2b2d32] bg-[#191b20] p-5 sm:p-6"
            >
              <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
                <div className="flex min-w-0 gap-4">
                  <span className="grid size-11 shrink-0 place-items-center rounded-[8px] bg-[#2b2d32] text-[#4f81f7]">
                    <Building2 className="size-5" />
                  </span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-lg font-semibold">{item.legal_name}</h2>
                      <span
                        className={cn(
                          "rounded-full px-2.5 py-1 text-[10px] font-medium",
                          item.status === "verified"
                            ? "bg-[#248a4b]/10 text-success-fg"
                            : item.status === "pending"
                              ? "bg-[#e8c547]/15 text-[#e8c547]"
                              : "bg-[#c63d35]/10 text-danger-fg",
                        )}
                      >
                        {statusLabels[item.status]}
                      </span>
                    </div>
                    <p className="mt-2 text-sm text-[#9fa1b1]">
                      {kindLabels[item.kind]} · ИНН {item.inn}
                      {item.ogrn ? ` · ОГРН ${item.ogrn}` : ""}
                    </p>
                    <p className="mt-1 text-xs text-[#828491]">
                      Владелец: {item.owner_email} · Заявка от{" "}
                      {new Date(item.created_at).toLocaleDateString("ru-RU")}
                    </p>
                  </div>
                </div>
                {item.status === "pending" && (
                  <div className="w-full shrink-0 lg:w-[330px]">
                    <Input
                      value={notes[item.inn] ?? ""}
                      onChange={(event) =>
                        setNotes((current) => ({
                          ...current,
                          [item.inn]: event.target.value,
                        }))
                      }
                      placeholder="Комментарий к решению"
                      className="border-[#2b2d32] bg-[#191b20]"
                    />
                    <div className="mt-3 grid grid-cols-2 gap-2">
                      <Button
                        variant="outline"
                        disabled={decision.isPending}
                        onClick={() =>
                          decision.mutate({
                            inn: item.inn,
                            approved: false,
                            note: notes[item.inn],
                          })
                        }
                        className="border-[#c63d35]/30 text-danger-fg"
                      >
                        <X className="size-4" />
                        Отклонить
                      </Button>
                      <Button
                        disabled={decision.isPending}
                        onClick={() =>
                          decision.mutate({
                            inn: item.inn,
                            approved: true,
                            note: notes[item.inn] || "Реквизиты проверены",
                          })
                        }
                        className="bg-[#248a4b] text-white hover:bg-[#1f7540]"
                      >
                        {decision.isPending ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : (
                          <Check className="size-4" />
                        )}
                        Подтвердить
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
