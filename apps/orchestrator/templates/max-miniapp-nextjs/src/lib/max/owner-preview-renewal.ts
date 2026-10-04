/** Only a server-confirmed private owner preview installs this guard. */
const EDITOR_ORIGINS = ["https://yleum.ru", "https://www.yleum.ru", "https://constructor.lead-generator.ru"];
const SESSION_PATH = "/api/max/session";
const BOOTSTRAP_PATH = "/api/omnia/preview-session";
const REQUEST_TYPE = "omnia:preview-session:renew";
const RESULT_TYPE = "omnia:preview-session:result";

function denied(): Response {
  return Response.json({ error: "Сессия превью закончилась. Откройте превью в редакторе и повторите сохранение.", code: "preview_session_required" }, { status: 401 });
}

export function installOwnerPreviewFetch(boundary: Window = window, configuredOrigins: readonly string[] = EDITOR_ORIGINS): () => void {
  const editorOrigins = configuredOrigins.filter(value => {
    try { const url = new URL(value); return url.protocol === "https:" && url.origin === value; }
    catch { return false; }
  });
  const previous = boundary.fetch;
  const native = previous.bind(boundary);
  let active = true;
  let pending: Promise<boolean> | null = null;
  let cancelBridge: (() => void) | null = null;
  const authControllers = new Set<AbortController>();
  const authEvidence = async (input: RequestInfo | URL, init?: RequestInit) => {
    const controller = new AbortController();
    authControllers.add(controller);
    const timeout = boundary.setTimeout(() => controller.abort(), 10_000);
    try {
      const response = await native(input, { ...init, signal: controller.signal });
      const body = response.ok ? await response.json() : null;
      return { status: response.status, ok: response.ok, body };
    }
    finally { boundary.clearTimeout(timeout); authControllers.delete(controller); }
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
    if (!active || !editorOrigins.length || boundary.parent === boundary) return resolve(null);
    const nonce = boundary.crypto.randomUUID().replaceAll("-", "");
    const finish = (url: string | null) => {
      boundary.clearTimeout(timer);
      boundary.removeEventListener("message", listener);
      cancelBridge = null;
      resolve(url);
    };
    const listener = (event: MessageEvent) => {
      if (!active || event.source !== boundary.parent || !editorOrigins.includes(event.origin) ||
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
    pending = (async () => {
      const status = await probe();
      if (!active || status === "denied") return false;
      if (status === "preview") return true;
      const url = await capability();
      if (!active || !url) return false;
      try {
        const response = await authEvidence(url, {
          credentials: "include", cache: "no-store", redirect: "error", headers: { Accept: "application/json" },
        });
        const body = response.body;
        if (!active || body?.mode !== "preview" || body?.user?.id !== "preview") return false;
        return (await probe()) === "preview" && active;
      } catch { return false; }
    })().finally(() => { pending = null; });
    return pending;
  };
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
    if (!permitted || !active) return denied();
    // Do not consume, clone or rewrite bodies, revisions or operation keys.
    const response = await native(input, init);
    if (response.status === 401 && active) {
      // Repair authentication only. The original request may have reached a
      // provider; never replay it or consume its response on the caller's behalf.
      try { await ensure(); } catch { /* Return the original response even if repair fails. */ }
    }
    return response;
  };
  boundary.fetch = guarded;
  return () => {
    active = false;
    cancelBridge?.();
    for (const controller of authControllers) controller.abort();
    if (boundary.fetch === guarded) boundary.fetch = previous;
  };
}
