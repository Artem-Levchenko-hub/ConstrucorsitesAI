/**
 * BYO-VPS — клиент управления своими серверами как целями деплоя.
 * Обёртка над `apps/api/routers/deploy_targets.py`. Секрет (ключ/пароль)
 * уходит только на вход create; наружу сервер его не отдаёт.
 */

import { apiFetch } from "./client";
import type { Uuid } from "./types";

export type DeployTarget = {
  id: Uuid;
  label: string;
  ssh_host: string;
  ssh_port: number;
  ssh_user: string;
  auth_type: "key" | "password";
  has_secret: boolean;
  ssh_public_key: string | null;
  verify_status: "unverified" | "ok" | "failed";
  verify_detail: string | null;
  verified_at: string | null;
  created_at: string;
};

export type DeployTargetCreate = {
  label: string;
  ssh_host: string;
  ssh_port: number;
  ssh_user: string;
  auth_type: "key" | "password";
  /** Пароль (password) или приватный ключ (key). Для key можно не слать — тогда
   *  сервер сгенерит пару и вернёт публичный ключ для добавления на VPS. */
  secret?: string;
};

export type DeployTargetVerifyResult = {
  ok: boolean;
  verify_status: string;
  detail: string | null;
  docker_ok: boolean;
  docker_version: string | null;
};

export async function listDeployTargets(): Promise<DeployTarget[]> {
  return apiFetch<DeployTarget[]>(`/api/deploy-targets`);
}

export async function createDeployTarget(
  payload: DeployTargetCreate,
): Promise<DeployTarget> {
  return apiFetch<DeployTarget>(`/api/deploy-targets`, {
    method: "POST",
    json: payload,
  });
}

export async function verifyDeployTarget(
  targetId: Uuid,
): Promise<DeployTargetVerifyResult> {
  return apiFetch<DeployTargetVerifyResult>(
    `/api/deploy-targets/${targetId}/verify`,
    { method: "POST" },
  );
}

export async function deleteDeployTarget(targetId: Uuid): Promise<void> {
  await apiFetch<void>(`/api/deploy-targets/${targetId}`, { method: "DELETE" });
}

/** Назначить проекту цель деплоя (null = наш хостинг). PATCH /api/projects/{id}. */
export async function setProjectDeployTarget(
  projectId: Uuid,
  targetId: Uuid | null,
): Promise<unknown> {
  return apiFetch<unknown>(`/api/projects/${projectId}`, {
    method: "PATCH",
    json: { deploy_target_id: targetId },
  });
}
