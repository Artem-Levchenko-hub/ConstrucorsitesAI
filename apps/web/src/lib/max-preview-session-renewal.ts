/** Mint a new owner capability without changing the iframe URL or React tree. */
export function installPreviewSessionRenewal(options: {
  window: Window;
  origin: string;
  frame: () => Window | null | undefined;
  mint: () => Promise<{ url: string }>;
}): () => void {
  let active = true;
  let pending = false;
  const listener = async (event: MessageEvent) => {
    const frame = options.frame();
    const nonce = event.data?.nonce;
    if (!active || pending || !frame || event.source !== frame || event.origin !== options.origin ||
      event.data?.type !== "omnia:preview-session:renew" || typeof nonce !== "string" || !/^[a-f0-9]{32}$/.test(nonce)) return;
    pending = true;
    try {
      const result = await options.mint();
      const url = new URL(result.url);
      if (url.origin !== options.origin || url.protocol !== "https:" || url.username || url.password ||
        url.hash || url.pathname !== "/api/omnia/preview-session") throw new Error("Invalid preview scope");
      if (active && options.frame() === frame) {
        frame.postMessage({ type: "omnia:preview-session:result", nonce, url: url.href }, options.origin);
      }
    } catch {
      if (active && options.frame() === frame) {
        frame.postMessage({ type: "omnia:preview-session:result", nonce, error: "session_unavailable" }, options.origin);
      }
    } finally {
      pending = false;
    }
  };
  options.window.addEventListener("message", listener);
  return () => { active = false; options.window.removeEventListener("message", listener); };
}
