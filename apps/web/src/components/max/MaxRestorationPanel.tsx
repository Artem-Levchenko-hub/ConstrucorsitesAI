"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import type { MaxRestorationController } from "@/lib/use-max-restoration";
import type { RestoreState } from "@/lib/api/restorations";
import type { RestorationAdaptationReference } from "@/lib/api/messages";

const labels: Record<RestoreState, string> = {
  preparing: "Подготавливаем восстановление", checking: "Проверяем совместимость",
  ready: "Восстановление подготовлено", needs_changes: "Нужны изменения для совместимости",
  adapting: "Адаптируем выбранную версию",
  applying: "Применяем в редакторе", completed: "Версия восстановлена в редакторе",
  cancelled: "Подготовка отменена", failed: "Восстановление не завершено",
  reconciling: "Уточняем результат",
};
// What exactly lost contact. The server keeps the operation's phase: a cancel is only
// possible before apply was requested, so only `apply` may speak about applying.
const reconcilingText: Record<string, string> = {
  prepare: "Связь прервалась во время подготовки. Уточняем её состояние — применение не запускалось.",
  cancel: "Отменяем подготовку и ждём подтверждения. Применение не запускалось.",
  apply: "Связь прервалась во время применения. Уточняем, какая версия сейчас работает.",
};
// The server keeps machine text for operators. The owner gets the meaning; the stage and
// phase stay visible underneath so support can find the run.
const UNCHANGED = "Текущая версия и данные не изменены — откат можно запустить ещё раз.";
function failureText(error: string): { text: string; code: string | null } {
  const structured = /^generation (deadline exceeded|cancelled); /.exec(error);
  if (structured) {
    const stage = /stage=([a-z_]+)/.exec(error)?.[1] ?? null;
    const phase = /phase=([a-z_]+)/.exec(error)?.[1] ?? null;
    const code = [stage, phase].filter(Boolean).join(" / ") || null;
    if (structured[1] === "cancelled")
      return { text: `Адаптация остановлена по вашей отмене. ${UNCHANGED}`, code };
    const what = stage === "repair"
      ? "ИИ не успел исправить замечания проверки за отведённое время."
      : stage === "proof"
      ? "Проверка результата не завершилась за отведённое время."
      : "ИИ не успел адаптировать версию за отведённое время.";
    return { text: `${what} ${UNCHANGED}`, code };
  }
  // The repair window closes with its own plain-text messages, which carry no fields.
  if (error.startsWith("generation deadline exceeded"))
    return { text: `ИИ не успел завершить доработку за отведённое время. ${UNCHANGED}`, code: null };
  if (/^adaptation (generation (failed|cancelled)|activation was not completed)$/.test(error))
    return { text: `Адаптация остановилась до проверки результата. ${UNCHANGED}`, code: null };
  return { text: error, code: null };
}
function ReportList({ title, items }: { title: string; items: string[] }) {
  return items.length ? <div><h4 className="font-medium">{title}</h4>
    <ul className="list-disc space-y-1 pl-5">{items.map((item, index) => <li key={index}>{item}</li>)}</ul>
  </div> : null;
}
export function MaxRestorationPanel({ restoration: r, onPrepareAdapt, onAdapt }: {
  restoration: MaxRestorationController;
  onPrepareAdapt?: (prompt: string, reference: RestorationAdaptationReference) => boolean;
  onAdapt?: (prompt: string, reference: RestorationAdaptationReference) => void | Promise<void>;
}) {
  const operation = r.operation;
  const automatic = operation?.execution_policy === "automatic_when_safe";
  const retryPreparation = operation?.phase === "retry_prepare";
  const scope = useRef<object>({});
  const adaptationPending = useRef(false);
  const [adapting, setAdapting] = useState(false);
  useEffect(() => {
    scope.current = {};
    return () => { scope.current = {}; };
  }, [operation?.project_id, operation?.id, r.headChanged]);
  if (!operation && !r.busy && !r.error && !r.hasPendingRequest) return null;
  const report = operation && ["ready", "needs_changes", "applying", "completed"].includes(operation.state)
    ? operation.report : null;
  const catalogObserved = (report?.database_state === "present" || report?.database_state === "empty")
    && !!report.checks?.some(check => check.evidence === "observed_catalog" || check.evidence === "structural_rule");
  const retryCatalogPreparation = operation?.state === "needs_changes" && !catalogObserved;
  const failure = operation?.error ? failureText(operation.error) : null;
  return <section className="mx-4 my-3 max-w-full rounded-xl border border-border-default bg-surface-raised p-4 text-sm"
    aria-label="Восстановление версии" data-testid="max-restoration-panel">
    <h3 className="font-semibold" role="status">{operation?.state === "ready" && automatic
      ? "Проверка завершена — применяем автоматически"
      : operation ? labels[operation.state] : r.busy
      ? "Отправляем запрос на подготовку" : "Проверяем результат запроса"}</h3>
    <p className="mt-2 text-fg-secondary">Публикация — отдельный шаг. Восстановление в редакторе не публикует изменения.</p>
    {operation?.state === "reconciling" && <p className="mt-2" data-testid="max-restoration-reconciling">
      {reconcilingText[operation.phase] ?? "Уточняем состояние операции. Повторная проверка идёт автоматически."}</p>}
    {r.headChanged && <p role="alert" className="mt-2">Черновик изменился после подготовки. Отмените эту подготовку и проверьте выбранную версию заново.</p>}
    {operation && !report && ["preparing", "checking"].includes(operation.state)
      && <p className="mt-2">Проверяем сохранность данных. Результат появится здесь.</p>}
    {retryCatalogPreparation && <p className="mt-2" data-testid="max-restoration-catalog-unavailable">
      Текущий каталог базы данных не удалось подтвердить. Повторите подготовку восстановления, когда проект снова будет доступен. Платная адаптация не запущена.
    </p>}
    {report && <div className="mt-3 space-y-3 break-words">
      {!retryCatalogPreparation && <p>{operation?.state === "needs_changes"
        ? retryPreparation
          ? "Условия применения изменились. Отмените эту подготовку и запустите восстановление версии заново."
          : "Выбранной версии нужны изменения для работы с текущими данными."
        : report.mode === "exact" ? "Подготовлен выбранный код без запуска ИИ."
          : "Подготовлен вариант с адаптацией. Проверьте отличия."}</p>}
      {catalogObserved && report.database_state === "empty" && <p>В проверенной базе нет бизнес-записей. Перед применением сервер проверяет структуру и изолированную копию.</p>}
      {catalogObserved && report.database_state === "present" && <p>В базе есть бизнес-данные. Восстанавливаем код с сохранением текущих данных.</p>}
      <ReportList title="Что изменится" items={report.changes} />
      <ReportList title="Что проверено" items={(report.checks ?? [])
        .filter((check) => check.status === "compatible").slice(0, 8).map((check) => check.explanation)} />
      <ReportList title="Что сохраняется по результатам проверки" items={report.retained_data} />
      <ReportList title="Что будет недоступно" items={report.unavailable_features} />
      <ReportList title="Обратите внимание" items={report.warnings} />
      <ReportList title="Что мешает применению" items={report.blockers} />
      <ReportList title="Следующие действия" items={report.next_actions} />
    </div>}
    {(r.error || operation?.error) && <p role="alert" className="mt-3 break-words text-danger-fg">
      {r.error || failure?.text}
      {!r.error && failure?.code && <span className="mt-1 block text-xs text-fg-secondary"
        data-testid="max-restoration-failure-code">Код для поддержки: {failure.code}</span>}
    </p>}
    <div className="mt-3 flex flex-wrap gap-2">
      {operation?.state === "needs_changes" && !retryPreparation && catalogObserved && operation.can_cancel && report && onAdapt && <div>
        <Button data-testid="max-restoration-adapt" disabled={adapting || r.busy || r.hasPendingRequest || r.headChanged || !operation.base_draft_snapshot_id}
          onClick={async () => {
            const baseSnapshotId = operation.base_draft_snapshot_id;
            if (!baseSnapshotId || adaptationPending.current || r.busy || r.hasPendingRequest || r.headChanged) return;
            adaptationPending.current = true;
            setAdapting(true);
            const ticket = scope.current;
            const prompt = [
              "Верни экраны и функции выбранной исторической версии в новый черновик. Адаптируй её исходный код к текущей базе данных.",
              "Сохрани все текущие пользовательские данные, новые поля и их значения, файлы, связи и права пользователей. Не подменяй базу старой копией и не удаляй данные. Неоднозначные изменения не угадывай.",
              "Проверь создание, чтение, изменение и удаление данных и доступ разных пользователей. Сообщи, что проверено и какие ограничения остались. Сохрани результат как новую версию проекта. Не публикуй приложение.",
            ].join("\n\n");
            try {
              const reference = {
                operation_id: operation.id,
                expected_draft_snapshot_id: baseSnapshotId,
              };
              if (onPrepareAdapt && !onPrepareAdapt(prompt, reference)) return;
              if (await r.cancel() && scope.current === ticket) await onAdapt(prompt, reference);
            } finally {
              adaptationPending.current = false;
              setAdapting(false);
            }
          }}>{adapting ? "Запускаем адаптацию…" : "Адаптировать и восстановить"}</Button>
        <p className="mt-2 text-fg-secondary">Кнопка запустит ИИ после отмены подготовки. Он создаст новый черновик с прежними экранами и функциями для текущих данных. Потребуется расход лимита ИИ.</p>
      </div>}
      {operation?.state === "ready" && !automatic && operation.can_apply && report && <Button
        data-testid="max-restoration-apply" disabled={r.busy || r.headChanged || !!r.error || r.hasPendingRequest} onClick={() => void r.apply()}>
        Сделать текущей в редакторе
      </Button>}
      {retryCatalogPreparation && <Button data-testid="max-restoration-retry-preparation"
        variant="outline" disabled={r.busy} onClick={() => void r.reprepare()}>Повторить подготовку</Button>}
      {operation?.can_cancel && <Button variant="outline" disabled={r.busy} onClick={() => void r.cancel()}>Отменить подготовку</Button>}
      {(r.error || r.hasPendingRequest || operation?.state === "reconciling") && <Button
        variant="outline" disabled={r.busy} onClick={() => void r.retry()}>Повторить проверку</Button>}
    </div>
  </section>;
}
