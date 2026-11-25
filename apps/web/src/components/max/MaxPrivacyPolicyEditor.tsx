"use client";

import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api/client";
import { prepareMaxPrivacyDraft } from "@/lib/api/max-studio";

function PrepareDraftButton({ projectId, disabled, hasText, onPrepared }: {
  projectId: string;
  disabled: boolean;
  hasText: boolean;
  onPrepared: (value: string, reviewNote: string) => void;
}) {
  const prepare = useMutation({ mutationFn: () => prepareMaxPrivacyDraft(projectId), retry: false });
  return <>
    <div><Button type="button" variant="outline" disabled={disabled || prepare.isPending}
      aria-busy={prepare.isPending}
      onClick={() => prepare.mutate(undefined, {
        onSuccess: draft => onPrepared(draft.text, draft.review_note ?? ""),
      })}>
      {prepare.isPending ? "Готовим черновик…" : hasText ? "Подготовить новый черновик" : "Подготовить политику"}
    </Button></div>
    {prepare.isError && <p role="alert" className="max-setup-group-note">
      {prepare.error instanceof ApiError && prepare.error.code === "project_empty"
        ? "Черновик можно подготовить после первой успешной сборки."
        : prepare.error instanceof ApiError && prepare.error.status === 409
          ? "Версия приложения изменилась. Обновите данные и подготовьте черновик ещё раз."
          : "Не удалось подготовить черновик. Попробуйте ещё раз."}
    </p>}
  </>;
}

export function MaxPrivacyPolicyEditor({ projectId, version, text, dirty, edited, reviewNote, onChange }: {
  projectId: string;
  version: number | undefined;
  text: string;
  dirty: boolean;
  edited: boolean;
  reviewNote?: string;
  onChange: (value: string, reviewNote?: string) => void;
}) {
  return <div className="max-setup-field max-setup-wide">
    {(text || edited) && <>
      <Label htmlFor="max-policy-text">Текст политики</Label>
      <Textarea id="max-policy-text" rows={12} maxLength={20_000}
        value={text} onChange={event => onChange(event.target.value)}
        aria-describedby={reviewNote ? "max-policy-hint max-policy-review-note" : "max-policy-hint"} />
    </>}
    <p id="max-policy-hint" className="max-setup-group-note">
      {edited && !text.trim() ? "После применения в приложении снова откроется стандартная политика."
        : edited ? "Перед применением проверьте состав данных, цели и основания обработки, сроки хранения и передачу во внешние сервисы."
        : dirty ? "Сохраните изменения, чтобы подготовить политику по актуальным данным."
        : "Черновик учитывает сохранённую версию приложения. Его можно отредактировать перед применением."}
    </p>
    {reviewNote && <p id="max-policy-review-note" role="note" className="max-setup-group-note">{reviewNote}</p>}
    <PrepareDraftButton key={`${projectId}:${version}:${dirty}`}
      projectId={projectId} disabled={dirty} hasText={Boolean(text)} onPrepared={onChange} />
  </div>;
}
