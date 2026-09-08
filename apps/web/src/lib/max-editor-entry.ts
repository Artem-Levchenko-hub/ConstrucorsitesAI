import type { SetupSection } from "@/components/max/MaxProjectSetupSections";

export type MaxEditorPanel = "max" | "hosting" | "services" | "publish";
export type MaxEditorEntry = MaxEditorPanel | `data:${SetupSection}` | "navigation" | "tools" | "preview";

export function readMaxEditorEntry(params: URLSearchParams): MaxEditorEntry | null {
  const data = params.get("data");
  if (data === "details" || data === "content" || data === "owner" || data === "policies") return `data:${data}`;
  const panel = params.get("panel");
  return panel === "max" || panel === "hosting" || panel === "services" || panel === "publish" ? panel : null;
}

/** undefined means normal navigation; null means return to this mounted editor. */
export function maxEditorLinkEntry(href: string, projectId: string, origin: string): MaxEditorEntry | null | undefined {
  const url = new URL(href, origin);
  if (url.origin !== origin || url.pathname !== `/max/${projectId}` || url.hash) return undefined;
  if (!url.search) return null;
  return readMaxEditorEntry(url.searchParams) ?? undefined;
}
