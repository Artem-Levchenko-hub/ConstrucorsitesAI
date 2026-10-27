"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { confirmPublicationMigration, getPublicationMigration, preparePublicationMigration } from "@/lib/api/max-studio";
import { MaxLaunchButton } from "./MaxLaunchButton";

const ACTIVE = new Set(["preparing", "applying", "reconciling"]);

export function MaxDataMigration({ projectId, snapshotId }: { projectId: string; snapshotId: string | null }) {
  const client = useQueryClient();
  const key = ["publication-migration", projectId];
  const prepareKey = useRef<{ snapshotId: string | null; key: string; operationId: string | null } | null>(null);
  const invalidated = useRef<string | null>(null);
  const [now, setNow] = useState(Date.now);
  const status = useQuery({ queryKey: key, queryFn: () => getPublicationMigration(projectId), retry: false,
    refetchInterval: query => query.state.data && (ACTIVE.has(query.state.data.state) || query.state.data.effect_possible) ? 2_000 : false });
  const prepare = useMutation({
    mutationFn: () => {
      if (!prepareKey.current || prepareKey.current.snapshotId !== snapshotId) {
        prepareKey.current = { snapshotId, key: crypto.randomUUID(), operationId: null };
      }
      return preparePublicationMigration(projectId, prepareKey.current.key);
    },
    onSuccess: result => {
      client.setQueryData(key, result);
      if (prepareKey.current?.snapshotId === result.snapshot_id) prepareKey.current.operationId = result.operation_id;
      if (result.state === "failed") prepareKey.current = null;
    },
  });
  const confirm = useMutation({
    mutationFn: ({ operation, plan }: { operation: string; plan: string }) => confirmPublicationMigration(projectId, operation, plan),
    onSuccess: result => client.setQueryData(key, result),
  });
  const record = status.data;
  const current = record?.project_id === projectId && Boolean(snapshotId) && record.snapshot_id === snapshotId;
  const applied = current && record?.state === "applied" && !record.effect_possible && record.backup_verified && Boolean(record.plan_sha256);
  const uncertain = Boolean(record?.effect_possible) || record?.state === "reconciling";
  const busy = prepare.isPending || confirm.isPending || Boolean(record && ACTIVE.has(record.state));
  const unexpired = Boolean(record?.expires_at && Date.parse(record.expires_at) > now);
  const canConfirm = current && record?.state === "prepared" && record.backup_verified &&
    !uncertain && unexpired && Boolean(record.plan_sha256) && !status.isError && !confirm.isError && !busy;
  const needsObservation = status.isError || prepare.isError || confirm.isError;

  useEffect(() => {
    if (record?.state !== "prepared") return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [record?.state]);

  useEffect(() => {
    if (!applied || !record || invalidated.current === record.operation_id) return;
    invalidated.current = record.operation_id;
    for (const queryKey of [["max-readiness", projectId], ["deploy", projectId], ["project", projectId]]) {
      void client.invalidateQueries({ queryKey });
    }
  }, [applied, record, client, projectId]);

  async function observe() {
    const result = await status.refetch();
    if (result.isSuccess) { prepare.reset(); confirm.reset(); }
  }

  function startPreparation() {
    // A lost reply retains its key even if a status read races the first POST.
    // Only an observed terminal/expired plan or a changed snapshot starts anew.
    if (record && prepareKey.current?.operationId === record.operation_id && !record.effect_possible &&
      (["failed", "stale", "blocked"].includes(record.state) || record.state === "prepared" && !unexpired)) {
      prepareKey.current = null;
    }
    prepare.mutate();
  }

  return <section data-testid="max-migration-review" className="max-launch-notice" aria-label="Проверка изменения данных">
    <h3>Проверка перед обновлением</h3>
    {status.isPending ? <p role="status">Проверяем состояние…</p>
      : applied ? <><p>Изменения данных применены. Теперь можно опубликовать новую версию приложения.</p><MaxLaunchButton projectId={projectId} /></>
      : uncertain ? <p role="status">Уточняем результат изменения данных. Дождитесь подтверждения перед публикацией.</p>
      : busy ? <p role="status"><Loader2 className="inline size-4 animate-spin" /> {record?.state === "applying" ? "Применяем проверенные изменения…" : "Создаём резервную копию и проверяем обновление на её копии…"} Можно закрыть окно — проверка продолжится.</p>
      : canConfirm ? <>
        <p>Копия восстановлена и проверена. Пробное обновление сохранило существующие записи.</p>
        <p>Добавятся необязательные поля: {record!.fields.length}. Существующие поля не меняются.</p>
        <details><summary>Какие данные добавятся</summary><ul>{record!.fields.map(field => <li className="break-all" key={`${field.table}.${field.name}`}>{field.table}: {field.name}</li>)}</ul></details>
        <p>Подтверждение относится только к этой версии. После изменения данных потребуется отдельная публикация.</p>
        <Button onClick={() => confirm.mutate({ operation: record!.operation_id, plan: record!.plan_sha256! })}>Применить изменения данных</Button>
      </> : <>
        <p>Перед обновлением нужна проверенная резервная копия. Проверка сначала восстановит её в отдельной среде и проверит сохранность записей.</p>
        {record && <p role="status">{!current || record.state === "stale" || !unexpired ? "Версия или срок проверки изменились. Нужна новая проверка." : "Проверку не удалось подтвердить. Опубликованная версия продолжает работать."}</p>}
        {!needsObservation && <Button onClick={startPreparation}>Проверить и создать копию</Button>}
      </>}
    {needsObservation && <div role="alert"><p>Не удалось получить подтверждённый результат. Сначала уточните состояние проверки.</p><Button variant="outline" disabled={status.isFetching} onClick={() => void observe()}>Уточнить статус</Button></div>}
  </section>;
}
