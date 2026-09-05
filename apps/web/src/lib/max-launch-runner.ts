import { activateMaxIntegration } from "@/lib/api/max-integration";
import { deployProject, getLastDeploy } from "@/lib/api/runtime";
import type { DeployStatus } from "@/lib/api/types";
import { isMaxDeployActive, shouldStartMaxDeploy } from "@/lib/max-launch-state";

const LAUNCH_TIMEOUT_MS = 20 * 60_000;
const deadlineMessage = "Проверка публикации превысила время ожидания. Откройте статус публикации или повторите проверку.";

type SavedLaunch = {
  version: 1;
  phase: "new" | "requesting" | "deploying" | "activating";
  idempotencyKey: string;
  runId: string | null;
  deadlineAt: number;
  paused: boolean;
};

const key = (projectId: string) => `omnia:max:launch:${projectId}`;
const fresh = (): SavedLaunch => ({ version: 1, phase: "new", idempotencyKey: crypto.randomUUID(), runId: null, deadlineAt: Date.now() + LAUNCH_TIMEOUT_MS, paused: false });

export function readMaxLaunch(projectId: string): SavedLaunch | null {
  const raw = window.localStorage.getItem(key(projectId));
  if (!raw) return null;
  // Migrate old tab checkpoints. A missing operation must still be reconciled.
  if (raw === "new" || raw === "deploying" || raw === "activating") return { ...fresh(), phase: raw };
  try {
    const value = JSON.parse(raw) as SavedLaunch;
    if (value.version === 1 && ["new", "requesting", "deploying", "activating"].includes(value.phase)
      && typeof value.idempotencyKey === "string" && value.idempotencyKey.length > 0
      && (value.runId === null || typeof value.runId === "string")
      && typeof value.deadlineAt === "number" && Number.isFinite(value.deadlineAt)
      && typeof value.paused === "boolean") return value;
  } catch { /* Invalid local checkpoint is replaced only on an explicit launch. */ }
  return null;
}

function save(projectId: string, state: SavedLaunch) {
  window.localStorage.setItem(key(projectId), JSON.stringify(state));
}

/** An explicit retry keeps the operation/key, but gives its observation a new deadline. */
export function prepareMaxLaunch(projectId: string) {
  const state = readMaxLaunch(projectId) ?? fresh();
  if (state.paused || state.deadlineAt <= Date.now()) {
    state.deadlineAt = Date.now() + LAUNCH_TIMEOUT_MS;
    state.paused = false;
  }
  save(projectId, state);
}

function waitForPoll(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => { window.clearTimeout(timer); reject(signal.reason); };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, 2_000);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

export async function finishMaxLaunch(projectId: string, onStatus: (status: DeployStatus) => void) {
  const state = readMaxLaunch(projectId) ?? fresh();
  save(projectId, state);
  const controller = new AbortController();
  const deadline = window.setTimeout(() => controller.abort(new Error(deadlineMessage)), Math.max(0, state.deadlineAt - Date.now()));
  const signal = controller.signal;
  let discardCheckpoint = false;
  const checkDeadline = () => {
    if (Date.now() >= state.deadlineAt) controller.abort(new Error(deadlineMessage));
    signal.throwIfAborted();
  };
  try {
    checkDeadline();
    let deployment = await getLastDeploy(projectId, { signal });
    checkDeadline();
    // A queued response without an ID is the old no-deployment sentinel.
    const active = isMaxDeployActive(deployment.phase, deployment.run_id);
    if (shouldStartMaxDeploy(state.phase, deployment.phase, deployment.run_id)
      || (state.phase === "requesting" && !active)) {
      state.phase = "requesting";
      save(projectId, state); // Save the key BEFORE POST; a lost response is safely retryable.
      deployment = await deployProject(projectId, undefined, state.idempotencyKey, { signal });
      checkDeadline();
    }
    if (!deployment.run_id || deployment.phase === "idle") {
      discardCheckpoint = true;
      throw new Error("Сервер не нашёл операцию публикации. Запустите публикацию заново.");
    }
    if (state.runId && state.runId !== deployment.run_id) {
      // Never activate another run implicitly or retain an unrecoverable identity.
      // Clearing the checkpoint lets only the next explicit click create a new key.
      discardCheckpoint = true;
      throw new Error("Статус относится к другой публикации. Проверьте её результат или опубликуйте текущую версию заново.");
    }
    state.runId = deployment.run_id;
    state.phase = "deploying";
    save(projectId, state);
    for (;;) {
      checkDeadline();
      if (deployment.run_id !== state.runId || deployment.phase === "idle") {
        discardCheckpoint = true;
        throw new Error("Сервер больше не возвращает выбранную публикацию. Проверьте её статус или запустите публикацию заново.");
      }
      onStatus(deployment);
      if (deployment.phase === "failed" || deployment.phase === "cancelled") {
        discardCheckpoint = true;
        throw new Error(deployment.error || "Публикация не завершилась");
      }
      if (deployment.phase === "done") break;
      await waitForPoll(signal);
      checkDeadline();
      deployment = await getLastDeploy(projectId, { signal });
    }
    state.phase = "activating";
    save(projectId, state);
    const integration = await activateMaxIntegration(projectId, { signal });
    checkDeadline();
    window.localStorage.removeItem(key(projectId));
    return integration;
  } catch (error) {
    if (discardCheckpoint) window.localStorage.removeItem(key(projectId));
    else { state.paused = true; save(projectId, state); }
    throw signal.aborted ? signal.reason : error;
  } finally {
    window.clearTimeout(deadline);
  }
}
