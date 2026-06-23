"use client";

/**
 * Кнопка «Поделиться» в TopBar (share-link, НЕ прод-деплой — тот живёт в
 * RuntimeButton как «Опубликовать в прод»). Открывает модалку со стабильной
 * публичной ссылкой проекта — `/p/<slug>`. Эта ссылка постоянна и работает без входа:
 *   - static-проект → бэкенд отдаёт HTML текущего снапшота;
 *   - контейнер-апп (entities/fullstack) → редирект на живой dev/prod-URL
 *     (просыпается по запросу, см. wake-on-request), так что ссылка не «висит».
 *
 * URL строится тем же способом, что и превью (PreviewFrame): `${API_URL}/p/<slug>`,
 * где в проде `NEXT_PUBLIC_API_URL = https://constructor.lead-generator.ru`.
 */

import { Check, Copy, ExternalLink, Share2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { buildPublicUrl } from "@/lib/public-url";

export function PublishButton({ projectSlug }: { projectSlug: string }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const publicUrl = buildPublicUrl(projectSlug);

  async function copy() {
    try {
      await navigator.clipboard.writeText(publicUrl);
      setCopied(true);
      toast.success("Ссылка скопирована");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard может быть недоступен (старый браузер / отказ в правах) —
      // fail-soft: подсказываем скопировать вручную, не роняем UI.
      toast.error("Не удалось скопировать", {
        description: "Выдели ссылку и скопируй вручную.",
      });
    }
  }

  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => setOpen(true)}
        className="gap-1.5 h-7 px-2.5 text-xs"
        title="Поделиться публичной ссылкой на проект"
      >
        <Share2 className="h-3 w-3" />
        <span className="hidden 2xl:inline">Поделиться</span>
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-[480px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Share2 className="h-4 w-4" />
              Поделиться проектом
            </DialogTitle>
            <DialogDescription>
              Постоянная публичная ссылка — открывается у любого без входа в
              аккаунт. Делитесь готовым сайтом.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-1.5 py-2">
            <Label htmlFor="public-url">Ссылка на проект</Label>
            <div className="flex items-center gap-2">
              <Input
                id="public-url"
                value={publicUrl}
                readOnly
                spellCheck={false}
                onFocus={(e) => e.currentTarget.select()}
                className="font-mono text-xs"
              />
              <Button
                variant="secondary"
                size="sm"
                onClick={copy}
                className="shrink-0 gap-1.5"
                title="Скопировать ссылку"
              >
                {copied ? (
                  <Check className="h-4 w-4 text-emerald-400" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
                {copied ? "Готово" : "Копировать"}
              </Button>
            </div>
          </div>

          <DialogFooter>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Закрыть
            </Button>
            <Button asChild className="gap-2">
              <Link href={publicUrl} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="h-4 w-4" />
                Открыть
              </Link>
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
