/** Startup and installed guards use the same scoped owner-session proof. */
const EDITOR_ORIGINS = ["https://yleum.ru", "https://www.yleum.ru", "https://constructor.lead-generator.ru"];
const SESSION_PATH = "/api/max/session";
const BOOTSTRAP_PATH = "/api/omnia/preview-session";
const REQUEST_TYPE = "omnia:preview-session:renew";
const RESULT_TYPE = "omnia:preview-session:result";

// Shared across Provider instances and installed recovery guards in this window.
// A stale body fence cannot undo Set-Cookie. Do not release a sent cookie writer
// until fetch has settled its headers, even after teardown or a newer attempt.
const cookieTasks = new WeakMap<Window, Promise<void>>();
type SessionRead = { current: () => boolean; cancel: () => void };
const sessionReads = new WeakMap<Window, Set<SessionRead>>();

function cancelSupersededReads(boundary: Window): void {
  for (const read of sessionReads.get(boundary) ?? []) if (!read.current()) read.cancel();
}

// Only the fixed GET /api/max/session contract is readonly. Its promise may be
// released even if a transport ignores abort; never use this for cookie writers.
async function readSession<T>(boundary: Window, current: () => boolean,
  read: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const controller = new AbortController();
  let cancel = () => {};
  const cancelled = new Promise<never>((_, reject) => {
    cancel = () => {
      reject(new Error("Не удалось проверить безопасный вход. Попробуйте ещё раз."));
      controller.abort();
    };
  });
  const phase = { current, cancel };
  const pending = sessionReads.get(boundary) ?? new Set<SessionRead>();
  pending.add(phase);
  sessionReads.set(boundary, pending);
  const timeout = boundary.setTimeout(cancel, 10_000);
  try {
    if (!current()) cancel();
    return await Promise.race([cancelled, Promise.resolve().then(() => {
      controller.signal.throwIfAborted();
      return read(controller.signal);
    })]);
  } finally {
    boundary.clearTimeout(timeout);
    pending.delete(phase);
    if (!pending.size && sessionReads.get(boundary) === pending) sessionReads.delete(boundary);
  }
}

const unguarded = new WeakMap<typeof window.fetch, typeof window.fetch>();
function baseFetch(boundary: Window): typeof window.fetch {
  return unguarded.get(boundary.fetch) ?? boundary.fetch.bind(boundary);
}
function cookieAuth<T>(boundary: Window, current: () => boolean, task: () => Promise<T>): Promise<T | null> {
  cancelSupersededReads(boundary);
  const result = (cookieTasks.get(boundary) ?? Promise.resolve()).then(() => current() ? task() : null);
  const tail = result.then(() => {}, () => {});
  cookieTasks.set(boundary, tail);
  void tail.then(() => { if (cookieTasks.get(boundary) === tail) cookieTasks.delete(boundary); });
  return result;
}

function denied(): Response {
  return Response.json({ error: "Сессия превью закончилась. Откройте превью в редакторе и повторите сохранение.", code: "preview_session_required" }, { status: 401 });
}

function createOwnerRecovery(boundary: Window, configuredOrigins: readonly string[], options: {
  fetch?: typeof window.fetch;
  current?: () => boolean;
  withinAuthTask?: boolean;
} = {}) {
  const editorOrigins = configuredOrigins.filter(value => {
    try { const url = new URL(value); return url.protocol === "https:" && url.origin === value; }
    catch { return false; }
  });
  const native = options.fetch ?? baseFetch(boundary);
  let active = true;
  const current = () => active && (options.current?.() ?? true);
  let pending: Promise<boolean> | null = null;
  let cancelBridge: (() => void) | null = null;
  const cookieBodyControllers = new Set<AbortController>();
  const authEvidence = async (input: RequestInfo | URL, init?: RequestInit, cookieWriter = false) => {
    if (!cookieWriter) return readSession(boundary, current, async signal => {
      const response = await native(input, { ...init, signal });
      const body = response.ok ? await response.json() : null;
      return { status: response.status, ok: response.ok, body };
    });
    const controller = new AbortController();
    let timeout: number | undefined;
    try {
      const response = await native(input, { ...init, signal: controller.signal });
      // Before cookie headers settle, abort is not proof that the browser cannot
      // apply them later. Afterwards body timeout/teardown is safe for ordering.
      if (!current()) return { status: response.status, ok: response.ok, body: null };
      cookieBodyControllers.add(controller);
      timeout = boundary.setTimeout(() => controller.abort(), 10_000);
      const body = response.ok ? await response.json() : null;
      return { status: response.status, ok: response.ok, body };
    }
    finally { if (timeout !== undefined) boundary.clearTimeout(timeout); cookieBodyControllers.delete(controller); }
  };
  const probe = async (): Promise<"preview" | "expired" | "denied"> => {
    try {
      const response = await authEvidence(SESSION_PATH, { credentials: "include", cache: "no-store" });
      if (response.status === 401) return "expired";
      const body = response.body;
      return body?.mode === "preview" && body?.user?.id === "preview" ? "preview" : "denied";
    } catch { return "denied"; }
  };
  const capability = (): Promise<string | null> => new Promise(resolve => {
    if (!current() || !editorOrigins.length || boundary.parent === boundary) return resolve(null);
    const nonce = boundary.crypto.randomUUID().replaceAll("-", "");
    const finish = (url: string | null) => {
      boundary.clearTimeout(timer);
      boundary.removeEventListener("message", listener);
      cancelBridge = null;
      resolve(url);
    };
    const listener = (event: MessageEvent) => {
      if (!current() || event.source !== boundary.parent || !editorOrigins.includes(event.origin) ||
        event.data?.type !== RESULT_TYPE || event.data?.nonce !== nonce) return;
      try {
        const url = new URL(event.data.url);
        finish(url.protocol === "https:" && url.origin === boundary.location.origin &&
          url.pathname === BOOTSTRAP_PATH && !url.username && !url.password && !url.hash ? url.href : null);
      } catch { finish(null); }
    };
    const timer = boundary.setTimeout(() => finish(null), 10_000);
    cancelBridge = () => finish(null);
    boundary.addEventListener("message", listener);
    // Referrer-Policy=no-referrer intentionally hides the cabinet URL. Only
    // these explicit cabinet origins receive a noncredential renewal request.
    try {
      for (const origin of editorOrigins) boundary.parent.postMessage({ type: REQUEST_TYPE, nonce }, origin);
    } catch { finish(null); }
  });
  const ensure = (): Promise<boolean> => {
    if (pending) return pending;
    const recover = async () => {
      if (!current()) return false;
      const status = await probe();
      if (!current() || status === "denied") return false;
      if (status === "preview") return true;
      const url = await capability();
      if (!current() || !url) return false;
      try {
        const response = await authEvidence(url, {
          credentials: "include", cache: "no-store", redirect: "error", headers: { Accept: "application/json" },
        }, true);
        const body = response.body;
        if (!current() || body?.mode !== "preview" || body?.user?.id !== "preview") return false;
        return (await probe()) === "preview" && current();
      } catch { return false; }
    };
    pending = (options.withinAuthTask ? recover() : cookieAuth(boundary, current, recover))
      .then(result => result === true).finally(() => { pending = null; });
    return pending;
  };
  const dispose = () => {
    active = false;
    cancelBridge?.();
    cancelSupersededReads(boundary);
    for (const controller of cookieBodyControllers) controller.abort();
  };
  return { ensure, dispose };
}

/** Protected Provider entry: use its captured base transport, never the installed
 * recovery wrapper (which would recursively acquire this same queue). */
export function requestAuthenticatedSession(
  boundary: Window,
  native: typeof window.fetch,
  initData: string,
  current: () => boolean,
  onDenied: () => void,
): Promise<{ response: Response; recovered: boolean } | null> {
  return cookieAuth(boundary, current, async () => {
    let response = initData ? await native(SESSION_PATH, {
      method: "POST", credentials: "include", cache: "no-store",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }),
    }) : await readSession(boundary, current, signal => native(SESSION_PATH,
      { method: "GET", credentials: "include", cache: "no-store", signal }));
    if (!current()) return null;
    if (response.status === 401 || response.status === 403) onDenied();
    let recovered = false;
    if (!initData && response.status === 401) {
      const recovery = createOwnerRecovery(boundary, EDITOR_ORIGINS, { fetch: native, current, withinAuthTask: true });
      try { recovered = await recovery.ensure(); }
      finally { recovery.dispose(); }
      if (!current()) return null;
      if (recovered) {
        response = await readSession(boundary, current, signal => native(SESSION_PATH,
          { method: "GET", credentials: "include", cache: "no-store", signal }));
        if (!current()) return null;
      }
    }
    return { response, recovered };
  });
}

const startupRecovery = new WeakMap<Window, Map<string, Promise<boolean>>>();

/** Request a capability only from an explicitly trusted parent; never install a fetch guard here. */
export function recoverOwnerPreviewSession(boundary: Window, configuredOrigins: readonly string[] = EDITOR_ORIGINS): Promise<boolean> {
  if (boundary.parent === boundary) return Promise.resolve(false);
  // Only callers with the same explicit trust scope may share a capability.
  const scope = JSON.stringify(configuredOrigins);
  const attempts = startupRecovery.get(boundary) ?? new Map<string, Promise<boolean>>();
  const existing = attempts.get(scope);
  if (existing) return existing;
  const recovery = createOwnerRecovery(boundary, configuredOrigins);
  const pending = recovery.ensure().finally(() => {
    recovery.dispose();
    attempts.delete(scope);
    if (!attempts.size) startupRecovery.delete(boundary);
  });
  attempts.set(scope, pending);
  startupRecovery.set(boundary, attempts);
  return pending;
}

/** Only a server-confirmed private owner preview installs this guard. */
export function installOwnerPreviewFetch(boundary: Window = window, configuredOrigins: readonly string[] = EDITOR_ORIGINS,
  current: () => boolean = () => true): () => void {
  const previous = boundary.fetch;
  const native = baseFetch(boundary);
  const { ensure, dispose } = createOwnerRecovery(boundary, configuredOrigins, { fetch: native, current });
  let active = true;
  const waitForSession = (signal?: AbortSignal | null): Promise<boolean> => {
    if (!signal) return ensure();
    return new Promise((resolve, reject) => {
      const aborted = () => { signal.removeEventListener("abort", aborted); reject(signal.reason); };
      signal.addEventListener("abort", aborted, { once: true });
      ensure().then(result => { signal.removeEventListener("abort", aborted); resolve(result); }, error => {
        signal.removeEventListener("abort", aborted); reject(error);
      });
    });
  };
  const guarded: typeof window.fetch = async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), boundary.location.href);
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    if (url.origin !== boundary.location.origin || !url.pathname.startsWith("/api/") ||
      (url.pathname === SESSION_PATH && method !== "GET") || url.pathname === BOOTSTRAP_PATH) return native(input, init);
    // SDK actor preflight uses this public GET before its guarded business POST.
    // Internal session probes use captured native fetch, so guarding the caller's
    // GET renews expiry without recursion or replaying a business request.
    const signal = init?.signal !== undefined ? init.signal : (input instanceof Request ? input.signal : undefined);
    signal?.throwIfAborted();
    const permitted = await waitForSession(signal);
    signal?.throwIfAborted();
    if (!permitted || !active || !current()) return denied();
    // Do not consume, clone or rewrite bodies, revisions or operation keys.
    const response = await native(input, init);
    if (response.status === 401 && active && current()) {
      // Repair authentication only. The original request may have reached a
      // provider; never replay it or consume its response on the caller's behalf.
      try { await ensure(); } catch { /* Return the original response even if repair fails. */ }
    }
    return response;
  };
  unguarded.set(guarded, native);
  boundary.fetch = guarded;
  return () => {
    active = false;
    dispose();
    if (boundary.fetch === guarded) boundary.fetch = previous;
  };
}
