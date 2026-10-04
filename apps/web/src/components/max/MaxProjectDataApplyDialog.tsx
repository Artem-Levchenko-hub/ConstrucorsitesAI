"use client";

import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useOwnerChatQuota } from "@/hooks/useOwnerChatQuota";
import { FREE_CHAT_UPGRADE_MESSAGE, isFreeChatRefusal, refreshOwnerAfterAcceptedPrompt } from "@/lib/owner-chat-quota";
import { describeApiError } from "@/lib/api/errors";
import { OwnerChatQuotaNotice } from "@/components/workspace/OwnerChatQuotaNotice";
import { sendPrompt } from "@/lib/api/messages";
import { getMaxProjectConfig } from "@/lib/api/max-studio";
import type { MaxProjectConfig } from "@/lib/api/types";

export function MaxProjectDataApplyDialog({ config, onClose, onReturnFocus }: {
  config: MaxProjectConfig;
  onClose: () => void;
  onReturnFocus?: () => void;
}) {
  const router = useRouter();
  const quota = useOwnerChatQuota();
  const qc = useQueryClient();
  const submitting = useRef(false);
  const [terminalFailure, setTerminalFailure] = useState(false);
  // A lost response, double click or reload must replay this application of the
  // saved version, rather than charging for a second identical generation.
  const baseKey = `max-config-apply-${config.project_id}-${config.config_version}`;
  const storageKey = `omnia:${baseKey}`;
  const [attemptKey, setAttemptKey] = useState(() => {
    try { return sessionStorage.getItem(storageKey) || baseKey; }
    catch { return baseKey; }
  });
  const [attemptDispatched, setAttemptDispatched] = useState(() => {
    try { return Boolean(sessionStorage.getItem(storageKey)); } catch { return false; }
  });
  const quotaBlocksNewAttempt = quota.exhausted && (!attemptDispatched || terminalFailure);
  const prompt = `Примени сохранённые данные приложения, версия ${config.config_version}, к его экранам и поведению. Прочти актуальную конфигурацию в src/lib/omnia/max-config.ts. Используй название, описание, главное действие, аудиторию, возможности и оформление из раздела «Основное». Покажи активные элементы раздела «Контент» в подходящем каталоге, сохрани их id, названия, описания, цены и подписи действий. У позиции есть раздел (category), фотография (image_url), наличие (availability) и варианты выбора (options): группируй каталог по непустым разделам, показывай фото когда оно задано и нейтральную заглушку когда нет, честно отмечай наличие (позицию out_of_stock нельзя заказать), а при непустых options требуй выбрать вариант перед главным действием. Не придумывай фотографии, цены и размеры, которых владелец не вводил. Для данных, редактируемых владельцем, используй getYleumAppConfig из @/lib/omnia/integration-client: загружай их при открытии экрана и обновляй при возвращении в приложение; не копируй каталог в константы. Сохрани существующие пользовательские данные и работающие функции. Данные владельца и политики используй через управляемые страницы поддержки, конфиденциальности и условий; не переписывай защищённые файлы. Учти выбранные требования к согласиям в пользовательских сценариях. Галочка оплаты не означает подключённую платёжную систему: не имитируй оплату, рассылки или другие внешние операции без подключённого сервиса. Проверь сборку и сообщи, что изменилось и что требует подключения интеграции.`;
  const start = useMutation({
    mutationFn: async (retryTerminal: boolean) => {
      if (quota.exhausted && (!attemptDispatched || retryTerminal)) throw new Error(FREE_CHAT_UPGRADE_MESSAGE);
      const latest = await getMaxProjectConfig(config.project_id);
      if (latest.config_version !== config.config_version) {
        qc.setQueryData(["max-config", config.project_id], latest);
        throw new Error("Данные приложения изменились. Закройте это окно и откройте их заново.");
      }
      let key = attemptKey;
      if (retryTerminal) {
        key = `${baseKey}-${crypto.randomUUID()}`;
        // Retain this deliberate new attempt across reloads before dispatch.
        // A lost response must retry it, not allocate another paid generation.
        sessionStorage.setItem(storageKey, key);
        setAttemptKey(key);
        setTerminalFailure(false);
      }
      // Retain the first dispatch too: an unknown outcome may already have
      // consumed Free, and its same-key replay must remain available even
      // when the owner profile was pending or unavailable at dispatch.
      sessionStorage.setItem(storageKey, key);
      setAttemptDispatched(true);
      return sendPrompt(config.project_id, prompt, "topmix-v1", [], {
        skipClarify: true, idempotencyKey: key, maxConfigVersion: config.config_version,
      });
    },
    onSuccess: (result) => {
      refreshOwnerAfterAcceptedPrompt(qc);
      for (const key of ["messages", "generation", "project-versions"]) {
        void qc.invalidateQueries({ queryKey: [key, config.project_id] });
      }
      if (result.run_status === "failed" || result.run_status === "cancelled") {
        setTerminalFailure(true);
        return;
      }
      toast.success(result.replayed ? "Открываем результат применения данных" : "Задание передано в чат приложения");
      onClose();
      router.push(`/max/${config.project_id}`);
    },
    onError: error => {
      if (isFreeChatRefusal(error)) {
        setAttemptDispatched(false);
        try { sessionStorage.removeItem(storageKey); } catch { /* Quota remains server-enforced. */ }
        void quota.refresh();
      }
    },
  });

  return <Dialog open onOpenChange={(open) => { if (!open && !submitting.current) onClose(); }}>
    <DialogContent data-product-shell data-max-studio className="max-settings-dialog max-w-xl" onCloseAutoFocus={event => {
      if (onReturnFocus) { event.preventDefault(); onReturnFocus(); }
    }}>
      <DialogHeader>
        <DialogTitle>Применить данные к приложению</DialogTitle>
        <DialogDescription>
          Данные сохранены. ИИ доработает экраны: название, содержание, оформление и пользовательские сценарии.
        </DialogDescription>
      </DialogHeader>
      <p className="text-sm text-fg-secondary">Доработка расходует баланс владельца. Результат появится в редакторе; для опубликованного приложения затем потребуется публикация.</p>
      <p className="text-sm text-fg-secondary">После привязки названия и каталог смогут обновляться из настроек. Изменение функций и дизайна каждый раз требует запуска доработки.</p>
      <OwnerChatQuotaNotice limited={quota.limited} remaining={quota.remaining} />
      {start.error && <p role="alert" className="text-sm text-danger-fg">{describeApiError(start.error, "Не удалось передать задание. Повторите попытку.")}</p>}
      {terminalFailure && <p role="alert" className="text-sm text-danger-fg">Предыдущая доработка завершилась без результата. Можно запустить новую попытку по этим же данным; она расходует баланс.</p>}
      <div className="flex flex-wrap justify-end gap-3">
        <Button variant="secondary" disabled={start.isPending} onClick={onClose}>Пока только сохранить</Button>
        <Button disabled={start.isPending || quotaBlocksNewAttempt} onClick={() => {
          if (submitting.current) return;
          submitting.current = true;
          void start.mutateAsync(terminalFailure).catch(() => undefined).finally(() => { submitting.current = false; });
        }}>{start.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}{terminalFailure ? "Повторить доработку" : "Запустить доработку"}</Button>
      </div>
    </DialogContent>
  </Dialog>;
}
