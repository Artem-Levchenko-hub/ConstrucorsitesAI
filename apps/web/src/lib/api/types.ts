/**
 * Mirror of docs/01-api-contract.md.
 * Single source of truth lives there — this file must stay in sync with the
 * backend Pydantic models. Any drift = bug.
 */

export type Uuid = string;
export type IsoDateTime = string;

export type User = {
  id: Uuid;
  email: string;
  created_at: IsoDateTime;
  last_login_at: IsoDateTime | null;
};

export type ProjectTemplate =
  | "blank"
  | "landing"
  | "portfolio"
  | "blog"
  // V2 Phase A — runs as a Next.js + Drizzle dev container managed by the
  // orchestrator. Preview iframe points at the live container's dev_url
  // instead of the static /p/<slug> path.
  | "fullstack";

export type Project = {
  id: Uuid;
  owner_id: Uuid;
  name: string;
  slug: string;
  template: ProjectTemplate;
  current_snapshot_id: Uuid | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
};

export type Snapshot = {
  id: Uuid;
  project_id: Uuid;
  commit_sha: string;
  prompt_text: string | null;
  model_id: string | null;
  parent_id: Uuid | null;
  preview_url: string | null;
  is_rollback_target: boolean;
  created_at: IsoDateTime;
};

export type SnapshotWithFiles = Snapshot & {
  files: Record<string, string>;
};

export type MessageRole = "user" | "assistant" | "system";

export type Message = {
  id: Uuid;
  project_id: Uuid;
  snapshot_id: Uuid | null;
  role: MessageRole;
  content: string;
  model_id: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  created_at: IsoDateTime;
};

export type ModelProvider =
  | "anthropic"
  | "openai"
  | "yandex"
  | "alibaba"
  | "sber"
  | "google";
export type ModelTag = "fast" | "quality" | "budget";

export type Model = {
  id: string;
  display_name: string;
  provider: ModelProvider;
  price_rub_per_1k_in: number;
  price_rub_per_1k_out: number;
  context_window: number;
  recommended_for: ModelTag[];
  /** Optional: whether the gateway has the provider key for this model. */
  available?: boolean;
};

export type Charge = {
  id: Uuid;
  message_id: Uuid | null;
  amount_rub: number;
  description: string;
  created_at: IsoDateTime;
};

export type WalletState = {
  balance_rub: number;
  recent_charges: Charge[];
};

export type PromptResponse = {
  message_id: Uuid;
  snapshot_id: Uuid | null;
};

export type ApiErrorCode =
  | "validation_failed"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "rate_limited"
  | "wallet_empty"
  | "model_unavailable"
  | "internal_error"
  | "conflict"
  // V2 — surfaced from apps/api/services/orchestrator_client.
  | "orchestrator_unavailable"
  | "orchestrator_rejected";

/** V2 — full-stack runtime state, returned by /api/projects/:id/runtime*. */
export type RuntimeState =
  | "provisioning"
  | "running"
  | "paused"
  | "stopped"
  | "failed";

export type RuntimeStatus = {
  state: RuntimeState;
  container_name: string | null;
  port: number | null;
  dev_url: string | null;
  last_active_at: IsoDateTime | null;
  hibernate_after_seconds: number | null;
};

export type DeployPhase =
  | "queued"
  | "building"
  | "pushing"
  | "swapping"
  | "done"
  | "failed";

export type DeployStatus = {
  phase: DeployPhase;
  started_at: IsoDateTime | null;
  finished_at: IsoDateTime | null;
  prod_url: string | null;
  image_tag: string | null;
  error: string | null;
};

export type ApiErrorBody = {
  error: {
    code: ApiErrorCode;
    message: string;
    details?: Record<string, unknown>;
  };
};

/** WebSocket events on /api/ws/projects/:id (server → client). */
export type WsEvent =
  | { type: "snapshot.created"; data: { snapshot: Snapshot } }
  | {
      type: "preview.ready";
      data: { snapshot_id: Uuid; preview_url: string };
    }
  | { type: "llm.chunk"; data: { message_id: Uuid; delta: string } }
  | {
      type: "llm.done";
      data: {
        message_id: Uuid;
        tokens_in: number;
        tokens_out: number;
        cost_rub: number;
      };
    }
  | { type: "llm.error"; data: { message_id: Uuid; error: string } }
  | { type: "wallet.updated"; data: { balance_rub: number } }
  // V2 runtime/deploy events. The api router broadcasts these from the
  // orchestrator (Phase A — only `started` and `progress` may fire for now;
  // the rest land once orchestrator's hibernate/health loops are wired in).
  | { type: "runtime.started"; data: { runtime: RuntimeStatus } }
  | { type: "runtime.stopped"; data: { runtime: RuntimeStatus } }
  | { type: "runtime.crashed"; data: { error: string } }
  | { type: "deploy.progress"; data: { deploy: DeployStatus } }
  | { type: "deploy.done"; data: { deploy: DeployStatus } }
  | { type: "deploy.failed"; data: { error: string } };

export type WsEventType = WsEvent["type"];
