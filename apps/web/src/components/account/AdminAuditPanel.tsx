"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleAlert, History, Loader2 } from "lucide-react";

import { listAdminAudit } from "@/lib/api/admin";

export function AdminAuditPanel() {
  const audit = useQuery({
    queryKey: ["admin-audit"],
    queryFn: listAdminAudit,
    retry: false,
  });

  if (audit.isLoading) {
    return (
      <div className="grid min-h-[240px] place-items-center">
        <Loader2 className="size-6 animate-spin text-[#f15a38]" />
      </div>
    );
  }
  if (audit.isError) {
    return (
      <section className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-8 text-center">
        <CircleAlert className="mx-auto size-7 text-[#c63d35]" />
        <h2 className="mt-4 text-lg font-semibold">Журнал не загрузился</h2>
      </section>
    );
  }
  if (!audit.data?.length) {
    return (
      <section className="rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-10 text-center">
        <History className="mx-auto size-7 text-[#8d887f]" />
        <h2 className="mt-4 text-lg font-semibold">Изменений пока нет</h2>
        <p className="mt-2 text-sm text-[#6d6962]">
          Выдача прав и подтверждения появятся здесь.
        </p>
      </section>
    );
  }

  return (
    <div className="overflow-hidden rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7]">
      {audit.data.map((event) => (
        <article
          key={event.id}
          className="border-b border-[#e5e1d8] p-4 last:border-b-0 sm:p-5"
        >
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm font-medium">
              {event.actor_email} → {event.target_email}
            </p>
            <time className="text-xs text-[#8d887f]">
              {new Date(event.created_at).toLocaleString("ru-RU")}
            </time>
          </div>
          <p className="mt-2 text-xs leading-5 text-[#6d6962]">
            Роль: {String(event.details.before?.role ?? "—")} →{" "}
            {String(event.details.after?.role ?? "—")} · Статус:{" "}
            {String(event.details.before?.status ?? "—")} →{" "}
            {String(event.details.after?.status ?? "—")}
            {event.details.note ? ` · ${event.details.note}` : ""}
          </p>
        </article>
      ))}
    </div>
  );
}
