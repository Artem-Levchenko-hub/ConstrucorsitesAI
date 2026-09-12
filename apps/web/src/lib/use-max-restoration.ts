"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/restorations";
import type { ProjectVersion, Snapshot } from "@/lib/api/types";
import { ApiError } from "@/lib/api/client";
import { readMaxLaunch } from "@/lib/max-launch-runner";

type Request = (
  | { kind: "prepare"; payload: api.PrepareRestorationRequest }
  | { kind: "apply"; operationId: string; payload: api.ApplyRestorationRequest }
  | { kind: "cancel"; operationId: string }
) & { error?: string; rejected?: boolean };
const storageKey = (project: string) => `omnia:restore:request:${project}`;
function readRequest(project: string): Request | null {
  try {
    if (typeof window === "undefined") return null;
    const value = JSON.parse(window.localStorage.getItem(storageKey(project)) ?? "null");
    if (!value || !["prepare", "apply", "cancel"].includes(value.kind)) return null;
    if (value.kind !== "prepare" && typeof value.operationId !== "string") return null;
    if (value.kind !== "cancel" && (
      typeof value.payload?.idempotency_key !== "string"
      || !(value.payload.expected_draft_snapshot_id === null || typeof value.payload.expected_draft_snapshot_id === "string")
      || (value.kind === "prepare" && typeof value.payload.target_version_id !== "string")
      || (value.kind === "apply" && !Number.isInteger(value.payload.report_revision))
    )) return null;
    if (value.error !== undefined && typeof value.error !== "string") return null;
    if (value.rejected !== undefined && typeof value.rejected !== "boolean") return null;
    return value as Request;
  } catch { return null; }
}
const terminal = (state: api.RestoreState) => ["completed", "cancelled", "failed"].includes(state);
const running = (state: api.RestoreState) => ["preparing", "checking", "applying", "reconciling"].includes(state);
function newer(previous: api.RestoreOperation | undefined, next: api.RestoreOperation) {
  return previous && previous.revision > next.revision ? previous : next;
}

export function useMaxRestoration({ projectId, currentSnapshotId, onCompleted }: {
  projectId: string;
  currentSnapshotId: string | null;
  onCompleted: (snapshot: Snapshot) => void;
}) {
  const qc = useQueryClient();
  const inflight = useRef(new Set<string>());
  const [busyProjects, setBusyProjects] = useState<ReadonlySet<string>>(new Set());
  const requestKey = ["restoration-request", projectId];
  const saved = useQuery({ queryKey: requestKey, queryFn: () => readRequest(projectId),
    initialData: () => readRequest(projectId), enabled: false });
  const selection = useQuery<string | null>({ queryKey: ["restoration-selection", projectId],
    queryFn: () => null, initialData: null, enabled: false });
  const list = useQuery({
    queryKey: ["restorations", projectId],
    queryFn: ({ signal }) => api.listRestorations(projectId, signal),
    retry: false,
    refetchInterval: query => query.state.data?.enabled ? 5_000 : false,
  });
  const items = (list.data?.items ?? []).filter(item => item.project_id === projectId);
  const sorted = [...items].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const activeListed = sorted.find(item => !terminal(item.state));
  const selected = selection.data
    ? qc.getQueryData<api.RestoreOperation>(["restoration", projectId, selection.data]) : undefined;
  const operationId = activeListed && (!selected || terminal(selected.state))
    ? activeListed.id : selection.data ?? sorted[0]?.id ?? null;
  const detail = useQuery({
    queryKey: ["restoration", projectId, operationId],
    queryFn: async ({ signal }) => {
      const next = await api.getRestoration(projectId, operationId!, signal);
      if (next.project_id !== projectId || next.id !== operationId) throw new Error("Статус относится к другой операции");
      return newer(qc.getQueryData<api.RestoreOperation>(["restoration", projectId, operationId]), next);
    },
    enabled: operationId !== null,
    initialData: () => items.find(item => item.id === operationId),
    retry: false,
    refetchInterval: query => query.state.data && !terminal(query.state.data.state) ? 2_000 : false,
  });
  const operation = detail.data?.project_id === projectId && detail.data.id === operationId ? detail.data : null;
  const headChanged = !!operation && ["ready", "needs_changes"].includes(operation.state)
    && operation.base_draft_snapshot_id !== currentSnapshotId;

  useEffect(() => {
    if (!operation || operation.state !== "completed" || !operation.applied_snapshot) return;
    const snapshot = operation.applied_snapshot;
    if (snapshot.project_id !== projectId) return;
    const observedKey = ["restoration-observed", projectId, operation.id];
    if (qc.getQueryData(observedKey)) return;
    qc.setQueryData(observedKey, true);
    // A historical completed operation discovered on F5 must never rewind a newer HEAD.
    if (currentSnapshotId === operation.base_draft_snapshot_id) onCompleted(snapshot);
    else if (currentSnapshotId !== snapshot.id) {
      void qc.invalidateQueries({ queryKey: ["snapshots", projectId] });
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
    }
  }, [operation, projectId, currentSnapshotId, onCompleted, qc]);

  function saveRequest(request: Request | null) {
    // Storage is a retry aid; canonical operation discovery is always server-side.
    try {
      if (request) window.localStorage.setItem(storageKey(projectId), JSON.stringify(request));
      else window.localStorage.removeItem(storageKey(projectId));
    } catch { /* Private browsing may deny storage; keep the in-tab key. */ }
    qc.setQueryData(requestKey, request);
  }
  async function execute(request: Request) {
    if (inflight.current.has(projectId)) return;
    if (request.kind !== "cancel" && readMaxLaunch(projectId)) {
      saveRequest({ ...request, rejected: true, error: "Сначала проверьте результат публикации приложения." });
      return;
    }
    inflight.current.add(projectId);
    setBusyProjects(previous => new Set([...previous, projectId]));
    saveRequest(request);
    try {
      const next = request.kind === "prepare" ? await api.prepareRestoration(projectId, request.payload)
        : request.kind === "apply" ? await api.applyRestoration(projectId, request.operationId, request.payload)
          : await api.cancelRestoration(projectId, request.operationId);
      if (next.project_id !== projectId || (request.kind !== "prepare" && next.id !== request.operationId)) {
        throw new Error("Сервер вернул другую операцию. Повторите проверку.");
      }
      qc.setQueryData<api.RestoreOperation>(["restoration", projectId, next.id], previous => newer(previous, next));
      qc.setQueryData(["restoration-selection", projectId], next.id);
      saveRequest(null);
      void qc.invalidateQueries({ queryKey: ["restorations", projectId] });
      return next;
    } catch (error) {
      // API admission 4xx (including stale HEAD/report 409) occurs before runtime action.
      // A timeout/network failure remains uncertain and must reuse its original key.
      const rejected = error instanceof ApiError && error.status >= 400 && error.status < 500
        && error.status !== 408 && error.status !== 429;
      saveRequest({ ...request, rejected,
        error: error instanceof Error ? error.message : "Не удалось получить ответ сервера" });
      void qc.invalidateQueries({ queryKey: ["restorations", projectId] });
      if (request.kind !== "prepare") void qc.invalidateQueries({ queryKey: ["restoration", projectId, request.operationId] });
    } finally {
      inflight.current.delete(projectId);
      setBusyProjects(previous => { const next = new Set(previous); next.delete(projectId); return next; });
    }
  }
  async function prepare(version: ProjectVersion) {
    if (!list.data?.enabled || version.project_id !== projectId || !version.snapshot_id || inflight.current.has(projectId)) return;
    if (operation && !terminal(operation.state)) return;
    const stored = saved.data ?? readRequest(projectId);
    if (stored && !stored.rejected && (stored.kind !== "prepare" || stored.payload.target_version_id !== version.id)) return;
    const previous = stored?.rejected ? null : stored;
    const request: Request = previous?.kind === "prepare" && previous.payload.target_version_id === version.id
      ? previous : { kind: "prepare", payload: { target_version_id: version.id,
        expected_draft_snapshot_id: currentSnapshotId, idempotency_key: crypto.randomUUID() } };
    await execute(request);
  }
  async function apply() {
    if (!operation?.can_apply || !operation.report || operation.state !== "ready" || headChanged) return;
    const stored = saved.data ?? readRequest(projectId);
    if (stored && !stored.rejected && stored.kind !== "apply") return;
    const previous = stored?.rejected ? null : stored;
    await execute(previous?.kind === "apply" && previous.operationId === operation.id ? previous : {
      kind: "apply", operationId: operation.id, payload: {
        report_revision: operation.report.revision, expected_draft_snapshot_id: currentSnapshotId,
        idempotency_key: crypto.randomUUID(),
      },
    });
  }
  async function cancel() {
    if (!operation?.can_cancel) return false;
    const next = await execute({ kind: "cancel", operationId: operation.id });
    return next?.state === "cancelled";
  }
  function observeCancelled(next: api.RestoreOperation) {
    if (next.project_id !== projectId || next.state !== "cancelled") return;
    qc.setQueryData<api.RestoreOperation>(["restoration", projectId, next.id], previous => newer(previous, next));
    const request = saved.data ?? readRequest(projectId);
    if (request?.kind === "cancel" && request.operationId === next.id) saveRequest(null);
    void qc.invalidateQueries({ queryKey: ["restorations", projectId] });
  }
  async function retry() {
    const request = saved.data ?? readRequest(projectId);
    if (request && !request.rejected) await execute(request);
    else { saveRequest(null); await list.refetch(); if (operationId) await detail.refetch(); }
  }
  return {
    operation, enabled: list.isSuccess && list.data.enabled,
    loading: list.isPending, busy: busyProjects.has(projectId), headChanged,
    preparing: busyProjects.has(projectId) && saved.data?.kind === "prepare",
    active: !!operation && !terminal(operation.state), running: !!operation && running(operation.state),
    error: saved.data?.error ?? (list.error || detail.error)?.message ?? null,
    hasPendingRequest: !!saved.data && !saved.data.rejected, prepare, apply, cancel, observeCancelled, retry,
  };
}
export type MaxRestorationController = ReturnType<typeof useMaxRestoration>;
