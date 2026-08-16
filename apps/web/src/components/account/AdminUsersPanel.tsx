"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BadgeCheck,
  Ban,
  CircleAlert,
  KeyRound,
  Loader2,
  RotateCcw,
  Search,
  ShieldCheck,
  ShieldOff,
  UserRound,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  listAdminUsers,
  updateAdminUser,
  type AdminUser,
  type AdminUserUpdate,
} from "@/lib/api/admin";
import { cn } from "@/lib/utils";

function statusLabel(status: string) {
  if (status === "active") return "Активен";
  if (status === "suspended") return "Приостановлен";
  return "Удаление запрошено";
}

export function AdminUsersPanel({ currentEmail }: { currentEmail: string }) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const users = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => listAdminUsers(),
    retry: false,
  });
  const update = useMutation({
    mutationFn: ({
      user,
      change,
    }: {
      user: AdminUser;
      change: AdminUserUpdate;
    }) => updateAdminUser(user.id, change),
    onSuccess: (user) => {
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-audit"] });
      toast.success(`Аккаунт ${user.email} обновлён`);
    },
    onError: (error) =>
      toast.error("Не удалось обновить аккаунт", {
        description: error instanceof Error ? error.message : "Повторите попытку",
      }),
  });

  const visible = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("ru-RU");
    return (users.data ?? []).filter(
      (user) =>
        !needle ||
        user.email.toLocaleLowerCase("ru-RU").includes(needle) ||
        user.business?.legal_name
          .toLocaleLowerCase("ru-RU")
          .includes(needle) ||
        user.business?.inn.includes(needle),
    );
  }, [search, users.data]);

  if (users.isLoading) {
    return (
      <div className="grid min-h-[280px] place-items-center">
        <Loader2 className="size-6 animate-spin text-[#f15a38]" />
      </div>
    );
  }

  if (users.isError) {
    return (
      <section className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-8 text-center">
        <CircleAlert className="mx-auto size-7 text-[#c63d35]" />
        <h2 className="mt-4 text-lg font-semibold">Аккаунты не загрузились</h2>
        <p className="mt-2 text-sm text-[#6d6962]">
          {users.error instanceof Error
            ? users.error.message
            : "Повторите попытку"}
        </p>
        <Button
          variant="outline"
          className="mt-5"
          onClick={() => void users.refetch()}
        >
          Повторить
        </Button>
      </section>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-4">
        <label className="relative block">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[#aaa59b]" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Email, организация или ИНН"
            aria-label="Поиск аккаунтов"
            className="border-[#d8d4cb] bg-white pl-9"
          />
        </label>
      </div>

      <div className="space-y-3">
        {visible.map((user) => {
          const isSelf = user.email.toLowerCase() === currentEmail.toLowerCase();
          const pending = update.isPending && update.variables?.user.id === user.id;
          return (
            <article
              key={user.id}
              className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-5"
            >
              <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
                <div className="flex min-w-0 gap-4">
                  <span
                    className={cn(
                      "grid size-11 shrink-0 place-items-center rounded-[8px]",
                      user.is_admin
                        ? "bg-[#f15a38]/10 text-[#f15a38]"
                        : "bg-[#ece8df] text-[#6d6962]",
                    )}
                  >
                    {user.is_admin ? (
                      <ShieldCheck className="size-5" />
                    ) : (
                      <UserRound className="size-5" />
                    )}
                  </span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="break-all text-base font-semibold">
                        {user.email}
                      </h2>
                      {user.is_admin && (
                        <span className="rounded-full bg-[#f15a38]/10 px-2.5 py-1 text-[10px] font-medium text-[#c8472b]">
                          Администратор
                        </span>
                      )}
                      <span
                        className={cn(
                          "rounded-full px-2.5 py-1 text-[10px] font-medium",
                          user.status === "active"
                            ? "bg-[#248a4b]/10 text-[#248a4b]"
                            : "bg-[#c63d35]/10 text-[#c63d35]",
                        )}
                      >
                        {statusLabel(user.status)}
                      </span>
                    </div>
                    <p className="mt-2 text-xs leading-5 text-[#6d6962]">
                      Email:{" "}
                      {user.email_verified_at
                        ? "подтверждён"
                        : "не подтверждён"}{" "}
                      · Баланс: {Number(user.wallet_balance_rub).toLocaleString("ru-RU")} ₽
                    </p>
                    <p className="text-xs leading-5 text-[#8d887f]">
                      Создан {new Date(user.created_at).toLocaleDateString("ru-RU")}
                      {user.last_login_at
                        ? ` · Вход ${new Date(user.last_login_at).toLocaleDateString("ru-RU")}`
                        : " · Ещё не входил"}
                    </p>
                    {user.business && (
                      <p className="mt-2 text-xs leading-5 text-[#6d6962]">
                        {user.business.legal_name} · ИНН {user.business.inn} ·{" "}
                        {user.business.status === "verified"
                          ? "организация подтверждена"
                          : "организация ожидает проверки"}
                      </p>
                    )}
                  </div>
                </div>

                <div className="grid w-full gap-2 sm:grid-cols-2 xl:w-[430px]">
                  {!user.email_verified_at && (
                    <Button
                      variant="outline"
                      disabled={pending}
                      onClick={() =>
                        update.mutate({
                          user,
                          change: { email_verified: true },
                        })
                      }
                    >
                      <BadgeCheck className="size-4" />
                      Подтвердить email
                    </Button>
                  )}
                  {user.business?.status !== "verified" && user.business && (
                    <Button
                      variant="outline"
                      disabled={pending}
                      onClick={() =>
                        update.mutate({
                          user,
                          change: {
                            business_verified: true,
                            note: "Реквизиты проверены администратором",
                          },
                        })
                      }
                    >
                      <KeyRound className="size-4" />
                      Подтвердить бизнес
                    </Button>
                  )}
                  {user.role === "admin" ? (
                    <Button
                      variant="outline"
                      disabled={pending || isSelf}
                      onClick={() =>
                        update.mutate({
                          user,
                          change: { role: "user" },
                        })
                      }
                    >
                      <ShieldOff className="size-4" />
                      Снять права
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      disabled={pending}
                      onClick={() =>
                        update.mutate({
                          user,
                          change: { role: "admin" },
                        })
                      }
                    >
                      <ShieldCheck className="size-4" />
                      Сделать админом
                    </Button>
                  )}
                  {user.status === "active" ? (
                    <Button
                      variant="outline"
                      disabled={pending || isSelf}
                      className="border-[#c63d35]/30 text-[#c63d35]"
                      onClick={() =>
                        update.mutate({
                          user,
                          change: { status: "suspended" },
                        })
                      }
                    >
                      <Ban className="size-4" />
                      Приостановить
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      disabled={pending}
                      onClick={() =>
                        update.mutate({
                          user,
                          change: { status: "active" },
                        })
                      }
                    >
                      <RotateCcw className="size-4" />
                      Восстановить
                    </Button>
                  )}
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
