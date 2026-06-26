/**
 * In-memory mock backend for the demo path. Activated when
 * NEXT_PUBLIC_USE_MOCKS=true (default in dev). Lets the frontend
 * showcase the full UX before apps/api is wired up.
 *
 * State is module-scoped — survives client-side navigation but resets
 * on full page reload. That's intentional for a demo.
 */

import type {
  Charge,
  Message,
  Model,
  Project,
  ProjectTemplate,
  Snapshot,
  User,
  WalletState,
} from "./types";

/**
 * Mocks ON by default — apps/api isn't wired up yet. Flip this off
 * (`NEXT_PUBLIC_USE_MOCKS=false`) once the real backend is reachable.
 */
export const USE_MOCKS = process.env.NEXT_PUBLIC_USE_MOCKS !== "false";

const uuid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2) + Date.now().toString(36);

const sha40 = () =>
  Array.from({ length: 40 }, () =>
    Math.floor(Math.random() * 16).toString(16),
  ).join("");

const now = () => new Date().toISOString();

const slugify = (input: string) =>
  input
    .toLowerCase()
    .replace(/[^a-z0-9а-яё]+/gi, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 40) || "project";

export const MOCK_USER: User = {
  id: "u-demo",
  email: "demo@omnia.ai",
  created_at: "2026-04-01T10:00:00Z",
  last_login_at: now(),
};

export const MOCK_MODELS: Model[] = [
  {
    id: "claude-sonnet-4-6",
    display_name: "Claude Sonnet 4.6",
    provider: "anthropic",
    price_rub_per_1k_in: 0.3,
    price_rub_per_1k_out: 1.5,
    context_window: 200_000,
    recommended_for: ["quality"],
  },
  {
    id: "gpt-4.1",
    display_name: "GPT-4.1",
    provider: "openai",
    price_rub_per_1k_in: 0.4,
    price_rub_per_1k_out: 1.6,
    context_window: 128_000,
    recommended_for: ["fast", "quality"],
  },
  {
    id: "yandexgpt-5",
    display_name: "YandexGPT 5",
    provider: "yandex",
    price_rub_per_1k_in: 0.1,
    price_rub_per_1k_out: 0.4,
    context_window: 32_000,
    recommended_for: ["budget"],
  },
  {
    id: "qwen-3-coder",
    display_name: "Qwen 3 Coder",
    provider: "alibaba",
    price_rub_per_1k_in: 0.08,
    price_rub_per_1k_out: 0.32,
    context_window: 128_000,
    recommended_for: ["budget", "fast"],
  },
];

const TEMPLATE_LABELS: Record<ProjectTemplate, string> = {
  blank: "Чистый холст",
  landing: "Лендинг бизнеса",
  portfolio: "Портфолио",
  blog: "Простой блог",
  fullstack: "Full-stack SaaS",
  nextjs_entities: "SaaS на сущностях",
  code: "Код (любой язык)",
  realtime: "Мессенджер / realtime",
};

type Store = {
  projects: Project[];
  snapshots: Map<string, Snapshot[]>;
  messages: Map<string, Message[]>;
  wallet: WalletState;
};

const seedProjects = (): Project[] => {
  const seeds: Array<{ name: string; template: ProjectTemplate }> = [
    { name: "Кофейня в Казани", template: "landing" },
    { name: "Портфолио иллюстратора", template: "portfolio" },
  ];
  return seeds.map((s, i) => ({
    id: `p-${i + 1}`,
    owner_id: MOCK_USER.id,
    name: s.name,
    slug: slugify(s.name),
    template: s.template,
    current_snapshot_id: `s-${i + 1}-init`,
    created_at: new Date(Date.now() - (i + 1) * 86_400_000).toISOString(),
    updated_at: new Date(Date.now() - (i + 1) * 3_600_000).toISOString(),
  }));
};

const initialSnapshot = (project: Project): Snapshot => ({
  id: `s-${project.id}-init`,
  project_id: project.id,
  commit_sha: sha40(),
  prompt_text: null,
  model_id: null,
  parent_id: null,
  preview_url: null,
  is_rollback_target: false,
  created_at: project.created_at,
});

const seedStore = (): Store => {
  const projects = seedProjects();
  const snapshots = new Map<string, Snapshot[]>();
  const messages = new Map<string, Message[]>();
  for (const p of projects) {
    snapshots.set(p.id, [initialSnapshot(p)]);
    messages.set(p.id, []);
  }
  return {
    projects,
    snapshots,
    messages,
    wallet: {
      balance_rub: 100,
      recent_charges: [],
    },
  };
};

const STORE: Store = seedStore();

const sleep = (ms: number) => new Promise((res) => setTimeout(res, ms));

export const mockApi = {
  async listProjects(): Promise<Project[]> {
    await sleep(150);
    return [...STORE.projects].sort(
      (a, b) =>
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    );
  },

  async getProject(id: string): Promise<Project> {
    await sleep(80);
    const p = STORE.projects.find((x) => x.id === id);
    if (!p) throw new Error("not_found");
    return p;
  },

  async createProject(
    name: string,
    template: ProjectTemplate,
  ): Promise<Project> {
    await sleep(200);
    const project: Project = {
      id: `p-${STORE.projects.length + 1}`,
      owner_id: MOCK_USER.id,
      name,
      slug: slugify(name),
      template,
      current_snapshot_id: null,
      created_at: now(),
      updated_at: now(),
    };
    STORE.projects.push(project);
    const snap = initialSnapshot(project);
    project.current_snapshot_id = snap.id;
    STORE.snapshots.set(project.id, [snap]);
    STORE.messages.set(project.id, []);
    return project;
  },

  async deleteProject(id: string): Promise<void> {
    await sleep(120);
    STORE.projects = STORE.projects.filter((p) => p.id !== id);
    STORE.snapshots.delete(id);
    STORE.messages.delete(id);
  },

  async listSnapshots(projectId: string): Promise<Snapshot[]> {
    await sleep(100);
    return [...(STORE.snapshots.get(projectId) ?? [])].sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
  },

  async listMessages(projectId: string): Promise<Message[]> {
    await sleep(100);
    return [...(STORE.messages.get(projectId) ?? [])].sort(
      (a, b) =>
        new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );
  },

  async getModels(): Promise<Model[]> {
    await sleep(50);
    return [...MOCK_MODELS];
  },

  async getWallet(): Promise<WalletState> {
    await sleep(50);
    return {
      balance_rub: STORE.wallet.balance_rub,
      recent_charges: [...STORE.wallet.recent_charges].slice(0, 10),
    };
  },

  /**
   * Append a user message + return ids the workspace will reuse for streaming.
   * The actual stream is driven from `simulatePromptStream` in lib/ws-mock.ts.
   */
  beginPrompt(
    projectId: string,
    promptText: string,
    modelId: string,
  ): { userMessageId: string; assistantMessageId: string } {
    const userId = uuid();
    const assistantId = uuid();
    const messages = STORE.messages.get(projectId) ?? [];
    messages.push({
      id: userId,
      project_id: projectId,
      snapshot_id: null,
      role: "user",
      content: promptText,
      model_id: modelId,
      tokens_in: null,
      tokens_out: null,
      created_at: now(),
    });
    messages.push({
      id: assistantId,
      project_id: projectId,
      snapshot_id: null,
      role: "assistant",
      content: "",
      model_id: modelId,
      tokens_in: null,
      tokens_out: null,
      created_at: new Date(Date.now() + 1).toISOString(),
    });
    STORE.messages.set(projectId, messages);
    return { userMessageId: userId, assistantMessageId: assistantId };
  },

  appendChunk(projectId: string, messageId: string, delta: string) {
    const messages = STORE.messages.get(projectId);
    if (!messages) return;
    const m = messages.find((x) => x.id === messageId);
    if (m) m.content += delta;
  },

  finalizeMessage(
    projectId: string,
    messageId: string,
    tokensIn: number,
    tokensOut: number,
  ) {
    const messages = STORE.messages.get(projectId);
    if (!messages) return;
    const m = messages.find((x) => x.id === messageId);
    if (m) {
      m.tokens_in = tokensIn;
      m.tokens_out = tokensOut;
    }
  },

  registerSnapshot(
    projectId: string,
    promptText: string,
    modelId: string,
  ): Snapshot {
    const snaps = STORE.snapshots.get(projectId) ?? [];
    const project = STORE.projects.find((p) => p.id === projectId);
    const snap: Snapshot = {
      id: uuid(),
      project_id: projectId,
      commit_sha: sha40(),
      prompt_text: promptText,
      model_id: modelId,
      parent_id: project?.current_snapshot_id ?? null,
      preview_url: null,
      is_rollback_target: false,
      created_at: now(),
    };
    snaps.push(snap);
    STORE.snapshots.set(projectId, snaps);
    if (project) {
      project.current_snapshot_id = snap.id;
      project.updated_at = snap.created_at;
    }
    return snap;
  },

  attachPreview(projectId: string, snapshotId: string, url: string) {
    const snaps = STORE.snapshots.get(projectId);
    if (!snaps) return;
    const s = snaps.find((x) => x.id === snapshotId);
    if (s) s.preview_url = url;
  },

  rollback(projectId: string, snapshotId: string): Snapshot {
    const snaps = STORE.snapshots.get(projectId) ?? [];
    const target = snaps.find((s) => s.id === snapshotId);
    if (!target) throw new Error("not_found");
    target.is_rollback_target = true;

    const newSnap: Snapshot = {
      id: uuid(),
      project_id: projectId,
      commit_sha: sha40(),
      prompt_text: null,
      model_id: null,
      parent_id: target.id,
      preview_url: target.preview_url,
      is_rollback_target: false,
      created_at: now(),
    };
    snaps.push(newSnap);
    const project = STORE.projects.find((p) => p.id === projectId);
    if (project) {
      project.current_snapshot_id = newSnap.id;
      project.updated_at = newSnap.created_at;
    }
    return newSnap;
  },

  charge(amount: number, description: string, messageId: string | null) {
    STORE.wallet.balance_rub = Math.max(
      0,
      Math.round((STORE.wallet.balance_rub - amount) * 100) / 100,
    );
    const charge: Charge = {
      id: uuid(),
      message_id: messageId,
      amount_rub: -amount,
      description,
      created_at: now(),
    };
    STORE.wallet.recent_charges = [
      charge,
      ...STORE.wallet.recent_charges,
    ].slice(0, 20);
  },

  currentBalance(): number {
    return STORE.wallet.balance_rub;
  },

  templateLabel(t: ProjectTemplate): string {
    return TEMPLATE_LABELS[t];
  },
};
