import type { DeployPhase, DeployProgress, DeployStatus } from "@/lib/api/types";

/**
 * P02: what the launch panel says while a publication runs.
 *
 * The controller (format_version 2) reports durable substages, a heartbeat and
 * transferred bytes. Nothing here invents progress: an unknown total stays a
 * plain byte count, elapsed time comes from server timestamps, and a heartbeat
 * that stopped advancing is reported as "checking", never as success or failure.
 */
export const PUBLICATION_STAGE_LABELS: Record<string, string> = {
  preflight: "Проверяем исходники и права",
  source_wake: "Будим рабочую среду",
  source_schema: "Читаем структуру базы данных",
  capture_rootfs: "Снимаем образ окружения",
  capture_volumes: "Упаковываем код и зависимости",
  verify_artifacts: "Проверяем целостность пакета",
  resume_source: "Возвращаем редактор в работу",
  prepare_target: "Готовим площадку релиза",
  seed_data: "Переносим пакет на площадку",
  activate: "Переключаем версию",
  start_app: "Запускаем приложение",
  verify_runtime: "Проверяем работу и настройки",
  tls: "Настраиваем HTTPS",
  observe: "Подтверждаем доступность",
};

export const PUBLICATION_REASON_LABELS: Record<string, string> = {
  migration_required: "Нужна миграция данных: структура базы отличается от опубликованной.",
  schema_changed: "Приложение изменило структуру базы при запуске — публикация остановлена.",
  service_readiness_failed: "Приложение не ответило на проверку готовности.",
  tls_failed: "Не удалось включить HTTPS для адреса приложения.",
  public_probe_failed: "Публичный адрес не подтвердил новую версию.",
  gateway_missing: "Шлюз приложения не поднялся.",
  capture_missing: "Не удалось снять пакет с рабочей среды.",
  source_changed: "Исходники изменились во время публикации — опубликуйте текущую версию заново.",
  publication_disabled: "Публикация отключена для этого проекта.",
  publication_active: "Другая публикация ещё выполняется.",
  artifact_integrity: "Пакет повреждён — публикация остановлена без изменений.",
  deadline_exceeded: "Публикация не уложилась в отведённое время.",
  cancelled: "Публикация остановлена.",
};

/** Heartbeats normally advance every ≤5 s; after this silence we say "checking". */
export const HEARTBEAT_STALE_MS = 45_000;

const PHASE_LABELS: Partial<Record<DeployPhase, string>> = {
  queued: "В очереди на публикацию",
  building: "Собираем приложение",
  pushing: "Передаём сборку на сервер",
  swapping: "Проверяем и переключаем версию",
  cancelling: "Останавливаем публикацию",
};

export function publicationStageLabel(status: Pick<DeployStatus, "phase" | "stage"> | undefined): string {
  if (!status) return "";
  if (status.stage && PUBLICATION_STAGE_LABELS[status.stage]) return PUBLICATION_STAGE_LABELS[status.stage];
  return PHASE_LABELS[status.phase] ?? "";
}

export function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds} с`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes} мин ${rest} с` : `${minutes} мин`;
}

/** Server-side elapsed: started_at → heartbeat_at (or finished_at); falls back to the client clock. */
export function publicationElapsedMs(status: Pick<DeployStatus, "started_at" | "heartbeat_at" | "finished_at"> | undefined, now = Date.now()): number | null {
  if (!status?.started_at) return null;
  const started = Date.parse(status.started_at);
  if (Number.isNaN(started)) return null;
  const last = status.finished_at ?? status.heartbeat_at;
  const end = last ? Date.parse(last) : now;
  return Number.isNaN(end) ? null : Math.max(0, end - started);
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1).replace(".", ",")} ГБ`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} МБ`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${bytes} Б`;
}

/** Bytes are shown as counts; a percentage appears only when the total is known. */
export function publicationBytesLabel(progress: DeployProgress | null | undefined): string | null {
  if (!progress || !progress.bytes_done) return null;
  if (progress.bytes_total) {
    return `проверено ${formatBytes(progress.bytes_done)} из ${formatBytes(progress.bytes_total)}`;
  }
  return `передано ${formatBytes(progress.bytes_done)}`;
}

export function publicationFailureText(status: Pick<DeployStatus, "error" | "error_stage" | "reason_code"> | undefined): { title: string; detail: string | null } {
  const reason = status?.reason_code ? PUBLICATION_REASON_LABELS[status.reason_code] : undefined;
  const stage = status?.error_stage ? PUBLICATION_STAGE_LABELS[status.error_stage] : undefined;
  const title = reason ?? "Публикация не завершилась. Проверьте настройки и повторите.";
  const detail = [stage ? `Стадия: ${stage.toLowerCase()}` : null, status?.error ?? null].filter(Boolean).join(" · ") || null;
  return { title, detail };
}

export type HeartbeatWatch = { value: string | null; seenAt: number };

/** Tracks when the server's heartbeat last changed as observed by this client. */
export function observeHeartbeat(previous: HeartbeatWatch | null, heartbeatAt: string | null | undefined, now: number): HeartbeatWatch {
  const value = heartbeatAt ?? null;
  if (previous && previous.value === value) return previous;
  return { value, seenAt: now };
}

export function heartbeatStale(watch: HeartbeatWatch | null, now: number): boolean {
  return Boolean(watch?.value) && now - (watch as HeartbeatWatch).seenAt > HEARTBEAT_STALE_MS;
}
