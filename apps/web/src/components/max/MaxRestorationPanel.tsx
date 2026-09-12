"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import type { MaxRestorationController } from "@/lib/use-max-restoration";
import type { RestoreState } from "@/lib/api/restorations";
import type { RestorationAdaptationReference } from "@/lib/api/messages";

const labels: Record<RestoreState, string> = {
  preparing: "Подготавливаем восстановление", checking: "Проверяем совместимость",
  ready: "Восстановление подготовлено", needs_changes: "Нужны изменения для совместимости",
  applying: "Применяем в редакторе", completed: "Версия восстановлена в редакторе",
  cancelled: "Подготовка отменена", failed: "Восстановление не завершено",
  reconciling: "Уточняем результат",
};
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
  return <section className="mx-4 my-3 max-w-full rounded-xl border border-border-default bg-surface-raised p-4 text-sm"
    aria-label="Восстановление версии" data-testid="max-restoration-panel">
    <h3 className="font-semibold" role="status">{operation ? labels[operation.state] : r.busy
      ? "Отправляем запрос на подготовку" : "Проверяем результат запроса"}</h3>
    <p className="mt-2 text-fg-secondary">Публикация — отдельный шаг. Восстановление в редакторе не публикует изменения.</p>
    {operation?.state === "reconciling" && <p className="mt-2">Связь прервалась во время применения. Уточняем, какая версия сейчас работает.</p>}
    {r.headChanged && <p role="alert" className="mt-2">Черновик изменился после подготовки. Отмените эту подготовку и проверьте выбранную версию заново.</p>}
    {operation && !report && ["preparing", "checking"].includes(operation.state)
      && <p className="mt-2">Проверяем сохранность данных. Результат появится здесь.</p>}
    {report && <div className="mt-3 space-y-3 break-words">
      <p>{operation?.state === "needs_changes"
        ? "Выбранной версии нужны изменения для работы с текущими данными."
        : report.mode === "exact" ? "Подготовлен выбранный код без запуска ИИ."
          : "Подготовлен вариант с адаптацией. Проверьте отличия."}</p>
      {report.database_state === "empty" && <p>В проверенной базе нет бизнес-записей. Восстановление сохраняет текущую базу и файлы.</p>}
      {report.database_state === "present" && <p>В базе есть бизнес-данные. Восстанавливаем код с сохранением текущих данных.</p>}
      <ReportList title="Что изменится" items={report.changes} />
      <ReportList title="Что сохраняется по результатам проверки" items={report.retained_data} />
      <ReportList title="Что будет недоступно" items={report.unavailable_features} />
      <ReportList title="Обратите внимание" items={report.warnings} />
      <ReportList title="Что мешает применению" items={report.blockers} />
      <ReportList title="Следующие действия" items={report.next_actions} />
    </div>}
    {(r.error || operation?.error) && <p role="alert" className="mt-3 break-words text-danger-fg">
      {r.error ?? operation?.error}
    </p>}
    <div className="mt-3 flex flex-wrap gap-2">
      {operation?.state === "needs_changes" && operation.can_cancel && report && onAdapt && <div>
        <Button data-testid="max-restoration-adapt" disabled={adapting || r.busy || r.hasPendingRequest || r.headChanged || !operation.base_draft_snapshot_id}
          onClick={async () => {
            const baseSnapshotId = operation.base_draft_snapshot_id;
            if (!baseSnapshotId || adaptationPending.current || r.busy || r.hasPendingRequest || r.headChanged) return;
            adaptationPending.current = true;
            setAdapting(true);
            const ticket = scope.current;
            const prompt = [
              "Верни экраны и функции выбранной исторической версии в новый черновик. Адаптируй её исходный код к текущей базе данных.",
              "Сохрани все текущие записи, новые поля и их значения, файлы, связи и права пользователей. Не подменяй базу старой копией и не удаляй данные. Неоднозначные изменения не угадывай.",
              "Проверь чтение, запись и доступ разных пользователей. Сообщи, что проверено и какие ограничения остались. Сохрани результат новой версией в истории. Не публикуй приложение.",
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
      {operation?.state === "ready" && operation.can_apply && report && <Button
        data-testid="max-restoration-apply" disabled={r.busy || r.headChanged || !!r.error || r.hasPendingRequest} onClick={() => void r.apply()}>
        Сделать текущей в редакторе
      </Button>}
      {operation?.can_cancel && <Button variant="outline" disabled={r.busy} onClick={() => void r.cancel()}>Отменить подготовку</Button>}
      {(r.error || r.hasPendingRequest || operation?.state === "reconciling") && <Button
        variant="outline" disabled={r.busy} onClick={() => void r.retry()}>Повторить проверку</Button>}
    </div>
  </section>;
}
