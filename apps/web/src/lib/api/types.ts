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
  | "fullstack"
  // Base44-style: fixed entity-engine backend (DB + auth + CRUD out of the box)
  // + generative React frontend. Also a container-backed Next.js app.
  | "nextjs_entities"
  // Language-agnostic source (Python script, Go CLI, parser, …). NOT a website
  // and NOT container-backed — stored as files like a GitHub repo. The workspace
  // shows the «Код» tab + download/GitHub-push instead of a preview iframe.
  | "code"
  // Real-time app (messenger / chat / live feed / collab). Container-backed
  // Next.js on the `nextjs-realtime` substrate (SSE+Redis hub + membership ACL
  // + presence). Preview points at the live container, same as fullstack.
  | "realtime"
  // Vite + React SPA, no backend (the interactive escape hatch). Container-backed
  // WEB stack → preview points at the live dev container and «Код» uses the
  // agent-step live tree, same as fullstack. Kept in sync with the backend
  // `Template` literal (schemas/project.py).
  | "spa"
  // Container-backed but NOT browser UIs — a Telegram bot (aiogram) and a FastAPI
  // service. Listed so `project.template` typechecks; the workspace treats them
  // like `code` (no web preview iframe).
  | "tgbot"
  | "api";

export type Project = {
  id: Uuid;
  owner_id: Uuid;
  name: string;
  slug: string;
  template: ProjectTemplate;
  current_snapshot_id: Uuid | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  design_preset_id?: string;
  design_preset_name?: string;
  /** B5+B6 — import-from-GitHub. "imported" when the project was cloned from an
   *  external repo; absent/null for organically created projects. Mirrors
   *  ProjectPublic.source (apps/api schemas/project.py). */
  source?: string | null;
  /** The GitHub URL the project was imported from, or null for organic projects. */
  external_repo_url?: string | null;
  /** Per-project toggle: when true, AI-emitted <img data-omnia-gen> tags are
   *  resolved to real images via gpt-image-1. Default true. */
  image_gen_enabled?: boolean;
  /** Thumbnail of the current snapshot (rendered preview PNG), or null until the
   *  first preview render lands. Shown as the project card's mini preview. */
  preview_url?: string | null;
  /** V4.1b/V4.2b — the project this one was forked ("Remix this") from, or
   *  null/absent for organically created projects. Drives the workspace remix
   *  lineage badge and the viral return-edge attribution. Mirrors
   *  ProjectPublic.forked_from (apps/api schemas/project.py). */
  forked_from?: Uuid | null;
  /** V4 #3 transitive remix lineage — the *name* and *slug* of the source this
   *  project was forked from, resolved server-side (apps/api get_project) so the
   *  remix badge can attribute it ("ремикс <name>") and link to /p/<slug>.
   *  null/absent for organic projects (and when the source was deleted). */
  forked_from_name?: string | null;
  forked_from_slug?: string | null;
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

/**
 * Element the user picked in the preview (select-mode), with their per-element
 * comment. Wire shape — mirrors apps/api schemas/message.py:SelectedElement.
 * The picker assigns a transient client id (see store/inspector.ts); it is not
 * part of this persisted/sent shape.
 */
export type SelectedElement = {
  selector: string;
  /** Short `tag#id.class` for the chip label. */
  label?: string | null;
  /** Truncated outerHTML — helps the model find the element in the source. */
  html?: string | null;
  /** Truncated visible text. */
  text?: string | null;
  /** Per-element instruction, e.g. "сделай красной". */
  comment?: string | null;
};

/** Approximate cost in rubles for this assistant message. Filled client-side
 * by `usePromptStream` from the `llm.done` event payload — not persisted in
 * the DB row (the wallet charge persists separately). Optional because
 * historical messages predating this field have no value. */
export type Message = {
  id: Uuid;
  project_id: Uuid;
  snapshot_id: Uuid | null;
  role: MessageRole;
  content: string;
  model_id: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  /** Approximate ruble cost — set client-side from `llm.done` event payload. */
  cost_rub?: number | null;
  /** Select-mode context attached to a user message (for chat-history chips). */
  selected_elements?: SelectedElement[] | null;
  /** Persisted agentic transcript — hydrated into the ["agent-steps",…] cache on
   *  history load so AgentTranscript re-renders after a reload. */
  agent_steps?: AgentStep[] | null;
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

/** A font the in-preview picker can apply, from `GET /api/fonts`. */
export type FontOption = {
  family: string;
  category: string;
  google_fonts_url: string;
  css_stack: string;
};

/**
 * Direct style edit sent to `POST /api/projects/:id/style-patch`. `tokens` are
 * site-wide CSS-var changes; `elements` are per-selector color/font overrides.
 * Persisted as a snapshot (no LLM). Colors are hex; `font_family` must be a
 * family from `GET /api/fonts`.
 */
export type StylePatchPayload = {
  tokens: { var: string; value: string }[];
  elements: {
    selector: string;
    color?: string;
    background_color?: string;
    border_color?: string;
    font_family?: string;
    /** Hide the element (display:none) — the "remove element" action. */
    hidden?: boolean;
  }[];
};

/**
 * One of the 8 server-defined design presets, as served by
 * `GET /api/design-presets`. The onboarding quiz renders these as style/palette
 * cards (swatches from `palette`); picking one sets the project's preset via
 * `PUT /api/projects/:id/design-preset`.
 */
export type DesignPresetPublic = {
  id: string;
  name: string;
  one_liner: string;
  reference_url: string;
  /** HEX values keyed by role: bg, bg_alt, fg, muted, accent, border. */
  palette: Record<string, string>;
  fonts: Record<string, string>;
  hero_type: string;
  industries: string[];
};

export type WalletState = {
  balance_rub: number;
  recent_charges: Charge[];
  // First-N free generations remaining (wow-effect onboarding). Optional so a
  // stale backend response without the fields doesn't break the type.
  free_generations_left?: number;
  free_generation_limit?: number;
};

// How the server will handle a prompt turn (mirrors api PromptResponse.mode):
//   "build"   — full (re)generation of the page
//   "edit"    — surgical, scoped change (preserves the rest of the page)
//   "clarify" — no generation this turn; the server is asking questions first
export type TurnMode = "build" | "edit" | "clarify";

export type PromptResponse = {
  message_id: Uuid;
  snapshot_id: Uuid | null;
  // Optional so a stale frontend reading an older API still type-checks; the
  // hook defaults a missing value to "build".
  mode?: TurnMode;
  // Progressive-discovery quick replies for a "clarify" turn: short tappable
  // chip answers to the streamed question, plus whether the free-text path
  // stays open. Absent / empty on build/edit turns. Optional → older API still
  // type-checks.
  choices?: string[];
  allow_custom?: boolean;
  // True when several chips can apply at once — the UI renders toggle chips + a
  // «Готово» button so the user picks a set in one turn (мультивыбор). Optional →
  // older API still type-checks; absent defaults to single-select.
  multi_select?: boolean;
  // Onboarding-popup framing (NORTH STAR pillar 2): the 1-based position of this
  // question and the planned batch size, so the workspace frames discovery as a
  // guided popup with a «Вопрос N из M» counter — plus the inferred niche label
  // for the framing banner. Absent on build/edit turns and the legacy
  // per-question path (no upfront plan → unknown total) → older API type-checks.
  question_index?: number | null;
  question_total?: number | null;
  niche?: string | null;
  // Onboarding LIVE-causality (pillar 2 — «вас услышали»): short «✓ …» recap
  // chips of the answers gathered so far. Absent on the first question and on
  // build/edit turns → older API still type-checks.
  recap?: string[] | null;
  // Onboarding LIVE design-preview (NORTH STAR pillars 2×3 — «покажи ЧТО
  // построим»): resolved design tokens the gathered answers steer toward, so the
  // popup paints a live mini-hero that morphs on every answer instead of only
  // echoing words. Absent on the first question and on build/edit turns → older
  // API still type-checks. Mirrors api chip_pixel_gate.spec_preview's payload.
  design_preview?: DesignPreview | null;
  // Upfront onboarding SURVEY (owner 2026-06-19 — «несколько вопросов сразу»): on
  // the FIRST discovery turn of a web build the server returns the WHOLE planned
  // batch (+ a clickable palette question) so the workspace shows ONE popup form
  // instead of a chat turn per question. Absent on code builds, follow-up turns,
  // and build/edit turns → older API still type-checks.
  survey?: SurveyQuestion[] | null;
  // Async onboarding (2026-07-01): true when the server deferred the slow
  // question-planning out of the request (Opus ~60-70s > the 30s client budget).
  // The assistant turn streams a short placeholder now; the real `survey` arrives
  // over the WebSocket (`onboarding.survey`) when ready. Absent/false on every
  // other turn → older API still type-checks.
  survey_pending?: boolean;
};

// One question in the upfront onboarding survey popup (owner 2026-06-19).
// `kind: "text"` → chips + free-text «Другое» (DiscoveryChips); `kind: "palette"`
// → clickable preset swatches carried in `options`.
export type SurveyQuestion = {
  message: string;
  kind: "text" | "palette";
  choices: string[];
  allow_custom: boolean;
  multi_select: boolean;
  // For kind === "palette": selectable presets — {id, name, one_liner, bg, accent}.
  options: { id: string; name: string; one_liner: string; bg: string; accent: string }[];
};

// Resolved design tokens for the onboarding live-preview mini-hero (pillars 2×3).
// Each axis is optional/nullable — the popup only paints what the user has
// decided so far (an undecided axis falls back to the UI's own neutral).
export type DesignPreview = {
  // Concrete accent HEX (e.g. "#AA3EDA"), or null until a colour family is picked.
  accent?: string | null;
  // The accent colour family name (e.g. "violet"), or null.
  accent_family?: string | null;
  // Dark canvas (true) / light canvas (false), or null when theme is undecided.
  dark_mode?: boolean | null;
  // Canonical tone token (premium / friendly / playful / minimal / corporate).
  tone?: string | null;
  // Canonical section keys the build will include (catalog / testimonials / …).
  sections?: string[] | null;
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
  | "orchestrator_rejected"
  // GitHub export — apps/api/src/omnia_api/routers/github.py.
  | "github_not_connected"
  | "github_state_invalid"
  | "github_state_expired"
  | "github_unavailable"
  | "project_empty";

// === GitHub OAuth + Push (apps/api/src/omnia_api/schemas/github.py) ===

export type GithubStatus = {
  connected: boolean;
  login: string | null;
};

export type GithubConnectResponse = {
  authorize_url: string;
};

export type GithubPushRequest = {
  /** Имя репозитория. Должно матчить /^[A-Za-z0-9._-]+$/, max 100. */
  repo_name: string;
  /** По умолчанию приватный — безопасно для сгенерённого AI кода. */
  private?: boolean;
  /** Описание репо, max 350. */
  description?: string;
};

export type GithubPushResponse = {
  repo_url: string;
  full_name: string;
};

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

/**
 * Multipass pipeline stage names. Mirrors the constants emitted by
 * `apps/api/src/omnia_api/services/multipass_generator.py` — keep in sync.
 */
export type MultipassStage =
  | "skeleton"
  | "content"
  | "visual"
  | "assembly"
  // Freeform pipeline stages, in order, on the same llm.pass channel:
  // art_director/writer (art_director_writer.py) then the two post-writer
  // stages emitted by the orchestrator (messages.py): images, judge.
  | "art_director"
  | "writer"
  | "images"
  | "judge";

/**
 * Client-side aggregate of `llm.pass` events for one assistant message.
 * Lives in React Query cache under key `["passes", projectId, messageId]`,
 * so `ChatMessage` re-renders the progress bar reactively without a Zustand
 * store. Cleared on `llm.done` / `llm.error`.
 */
/**
 * One step in the agentic builder's live transcript (see the `agent.step` WS
 * event). Accumulated per assistant message in React Query cache under
 * `["agent-steps", projectId, messageId]` and rendered by `AgentTranscript` as
 * a Claude-Code-style step list while the agent works.
 */
export type AgentStep = {
  step: number | null;
  kind: "step" | "escalate" | "stalled" | "retry";
  action: string;
  path: string;
  /** Raw tool name (write_file/build/…) for icon selection. */
  tool?: string;
  /** What the step did inside — content/output preview, shown on drill-in. */
  detail?: string;
  /** false = the step failed (drill-in shows the error). */
  ok?: boolean;
};

export type PassProgress = {
  current: MultipassStage | null;
  /**
   * Model working the current stage (e.g. "kimi-k2.6-thinking" / "deepseek-v4-pro"),
   * when the backend reports it on the active stage's `start` event. Null between
   * stages. Drives human narration ("Claude · композиция героя") in the build UI.
   */
  currentModel: string | null;
  completed: MultipassStage[];
};

/**
 * V3.10a — structured art-director brief delivered live (see the `omnia:brief`
 * event below). Cached per-message by usePromptStream and read by
 * StreamingPreviewFrame to narrate the design as it builds.
 */
export type StreamBrief = {
  palette: Record<string, string>;
  fonts: { display?: string; text?: string };
  motion: string;
  sections: Array<{ id: string; name: string }>;
};

/** WebSocket events on /api/ws/projects/:id (server → client). */
export type WsEvent =
  | { type: "snapshot.created"; data: { snapshot: Snapshot } }
  | {
      type: "preview.ready";
      data: { snapshot_id: Uuid; preview_url: string };
    }
  | {
      type: "llm.chunk";
      // `seq` — monotonic per-message counter (added for the resumable stream).
      // Lets the client dedup buffered vs live deltas and detect gaps after a
      // reconnect. Optional: pre-resumable backends omit it (treated as a plain
      // append, same as before).
      data: { message_id: Uuid; delta: string; seq?: number };
    }
  | {
      // Resumable stream: on (re)connect the server replays the cumulative
      // content of an in-flight generation as one frame, so a page refresh
      // mid-build no longer freezes the preview. Client replaces content and
      // resumes appending live deltas from `seq` onward (see usePromptStream).
      type: "stream.sync";
      data: { message_id: Uuid; content: string; seq: number };
    }
  | {
      // Live image drop-in: one event per generated picture as it resolves, so
      // the streaming preview swaps it into its frame in real time (instead of
      // every image appearing at once on the final snapshot). `idx` is the
      // image's position among index.html's data-omnia-gen <img> tags.
      type: "image.resolved";
      data: { message_id: Uuid; idx: number; url: string };
    }
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
  | {
      // App build/runtime failure surfaced as a chat card (messages.py +
      // services/app_errors.py). The card content itself is persisted into the
      // assistant message as an `<app-error …>` block; this event just tells the
      // client to refetch so it appears live without a manual reload.
      type: "app.error";
      data: { message_id: Uuid; category: string; title: string };
    }
  | {
      // Async onboarding (2026-07-01): the first-turn question batch was planned
      // out of band (Opus ~60-70s > the 30s POST budget) and arrives here. The
      // client stashes `survey` under ["onboarding-survey", projectId] — the same
      // cache key the synchronous HTTP path uses — so ChatPanel opens the popup.
      type: "onboarding.survey";
      data: {
        message_id: Uuid;
        survey: SurveyQuestion[];
        question_index?: number | null;
        question_total?: number | null;
        niche?: string | null;
      };
    }
  | {
      // Phase B.3 — multipass progress. Backend (multipass_generator.py)
      // emits start+end events for each of skeleton/content/visual/assembly
      // so the chat UI can show "Шаг 2/4: Контент" while the cheap model
      // crunches through the focused passes.
      type: "llm.pass";
      data: {
        message_id: Uuid;
        pass: MultipassStage;
        stage: "start" | "end";
        // Which model is working this stage (art-director / writer / judge).
        // Present on `start` for freeform stages (messages.py forwards it from
        // art_director_writer); enables human narration in the build UI.
        model?: string;
      };
    }
  | {
      // V3.10a — the art-director brief surfaced as a live event. The backend
      // (art_director_writer.parse_brief) extracts palette HEX / fonts / motion
      // signature / section list from the Pass-1 brief and fans it out before
      // llm.done, so the streaming preview can narrate the design reasoning
      // ("выбираю тёплую палитру… компоную герой") as the page is born (V3.10).
      type: "omnia:brief";
      data: {
        message_id: Uuid;
        palette: Record<string, string>;
        fonts: { display?: string; text?: string };
        motion: string;
        sections: Array<{ id: string; name: string }>;
      };
    }
  | { type: "wallet.updated"; data: { balance_rub: number } }
  // V2 runtime/deploy events. The api router broadcasts these from the
  // orchestrator (Phase A — only `started` and `progress` may fire for now;
  // the rest land once orchestrator's hibernate/health loops are wired in).
  | { type: "runtime.started"; data: { runtime: RuntimeStatus } }
  | { type: "runtime.stopped"; data: { runtime: RuntimeStatus } }
  | { type: "runtime.crashed"; data: { error: string } }
  | { type: "deploy.progress"; data: { deploy: DeployStatus } }
  | { type: "deploy.done"; data: { deploy: DeployStatus } }
  | { type: "deploy.failed"; data: { error: string } }
  | {
      // Agentic builder transcript (messages.py `_agent_emit`). One event per
      // loop step so the chat renders a live Claude-Code-style step list (tool +
      // path). `kind` separates a normal tool step from escalate/stalled/retry
      // signals. Cached per-message under ["agent-steps", projectId, messageId].
      type: "agent.step";
      data: {
        message_id: Uuid;
        step: number | null;
        kind: "step" | "escalate" | "stalled" | "retry";
        action: string;
        path: string;
        tool?: string;
        detail?: string;
        ok?: boolean;
      };
    };

export type WsEventType = WsEvent["type"];

// === Exe-build (Task 9) ===

/**
 * Lifecycle of a Windows .exe build triggered by «Собрать .exe».
 * Matches the exe.* event types emitted by the orchestrator over the project WS.
 */
export type ExeBuildStage =
  | "idle"      // not started (or reset after terminal state)
  | "starting"  // POST sent, waiting for first exe.* event
  | "build"     // exe.stage received — PyInstaller running
  | "heal"      // exe.heal received — doctor patching dependencies
  | "ready"     // exe.ready received — artefacts available for download
  | "failed";   // exe.failed received — unrecoverable error

/**
 * Payload of the terminal `exe.ready` WS event.
 * `setup_url` / `exe_url` are api-relative paths (e.g.
 * `/api/projects/<id>/exe/<build_id>/setup`) — cookie-authed same-origin
 * GETs so `<a href={…} download>` works without any fetch/blob dance.
 */
export interface ExeReadyData {
  build_id: string;
  /** Inno Setup installer — always present on success. */
  setup_url: string;
  /** Portable single-file .exe — may be null when the build only produced an installer. */
  exe_url: string | null;
  /** Human-readable artefact name (e.g. "MyApp-1.0-setup.exe"). */
  name: string;
  /** Installer file size in bytes (for the «N МБ» label). */
  size: number;
}
