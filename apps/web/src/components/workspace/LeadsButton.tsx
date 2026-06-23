"use client";

/**
 * «Заявки» в TopBar — owner inbox для лид-форм (P-LEAD). Показывает счётчик
 * пойманных заявок и открывает диалог со списком (что прислали через формы на
 * опубликованном сайте). Бэкенд: GET /api/projects/:id/leads (owner-scoped).
 */

import { useQuery } from "@tanstack/react-query";
import { Inbox, Loader2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { getLeads } from "@/lib/api/leads";

export function LeadsButton({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false);

  const { data, isPending, isError } = useQuery({
    queryKey: ["leads", projectId],
    queryFn: () => getLeads(projectId),
    retry: false,
    staleTime: 30_000,
  });

  const count = data?.count ?? 0;

  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => setOpen(true)}
        className="gap-1.5 h-7 px-2.5 text-xs"
        title="Заявки с сайта"
      >
        <Inbox className="h-3.5 w-3.5" />
        <span className="hidden 2xl:inline">Заявки</span>
        {count > 0 && (
          <span className="ml-0.5 rounded-full bg-primary px-1.5 text-[10px] font-semibold text-primary-foreground">
            {count}
          </span>
        )}
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Заявки с сайта</DialogTitle>
            <DialogDescription>
              Что присылают через формы на опубликованном сайте.
            </DialogDescription>
          </DialogHeader>

          <div className="max-h-[60vh] overflow-y-auto">
            {isPending ? (
              <div className="flex items-center justify-center py-10 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
              </div>
            ) : isError ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                Не удалось загрузить заявки.
              </p>
            ) : count === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                Пока заявок нет. Когда посетитель заполнит форму на сайте — она
                появится здесь.
              </p>
            ) : (
              <ul className="space-y-2">
                {data!.leads.map((lead) => (
                  <li
                    key={lead.id}
                    className="rounded-lg border border-border bg-card p-3 text-sm"
                  >
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <span className="text-xs text-muted-foreground">
                        {new Date(lead.created_at).toLocaleString("ru-RU")}
                      </span>
                      {lead.source && (
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                          {lead.source}
                        </span>
                      )}
                    </div>
                    <dl className="space-y-0.5">
                      {Object.entries(lead.data).map(([key, value]) => (
                        <div key={key} className="flex gap-2">
                          <dt className="shrink-0 font-medium text-muted-foreground">
                            {key}:
                          </dt>
                          <dd className="break-words">{value}</dd>
                        </div>
                      ))}
                    </dl>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
