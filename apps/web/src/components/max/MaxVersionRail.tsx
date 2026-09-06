"use client";

import type { ProjectVersion } from "@/lib/api/types";
import { projectVersionTitle, versionStatusLabel, versionImageUrl } from "@/lib/project-version";
import { cn } from "@/lib/utils";

export function MaxVersionRail({ versions, selectedVersionId, loading, error, hasOlder, loadingOlder, onLoadOlder, onSelect }: {
  versions: ProjectVersion[];
  selectedVersionId: string | null;
  loading: boolean;
  error?: boolean;
  hasOlder?: boolean;
  loadingOlder?: boolean;
  onLoadOlder?: () => void;
  onSelect: (versionId: string | null) => void;
}) {
  return <nav className="max-projects-scroll h-full w-[90px] shrink-0 overflow-y-auto overscroll-contain border-r border-[#25272b] px-1 py-2" aria-label="История версий" aria-busy={loading} data-testid="max-version-rail">
    {loading ? <p role="status" className="p-2 text-[10px]">Загружаем историю…</p> : !versions.length && <p className="p-2 text-[10px] text-[#828491]">{error ? "История недоступна" : "Версии появятся здесь"}</p>}
    <ol className="space-y-1">{versions.map((version) => <li key={version.id}>
      <button type="button" onClick={() => onSelect(version.id)} aria-pressed={selectedVersionId === version.id || (selectedVersionId === null && version.is_current)} aria-label={`Версия ${version.number}: ${projectVersionTitle(version)}, ${versionStatusLabel[version.status]}`} title={`v${version.number} · ${projectVersionTitle(version)} · ${new Date(version.created_at).toLocaleString("ru-RU")}`} data-testid={`max-version-${version.number}`} className={cn("w-full rounded-lg p-1.5 text-left text-[9px] hover:bg-[#2b2d32] focus-visible:outline-accent", (selectedVersionId === version.id || (selectedVersionId === null && version.is_current)) && "bg-accent/10")}>
        {version.previews[0] && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={versionImageUrl(version.previews[0].url)} alt="" loading="lazy" className="mb-1 h-10 w-full rounded object-cover object-top" />
        )}
        <span className="block font-semibold text-accent">v{version.number}{version.is_current ? " · текущая" : ""}</span>
        <span className="block truncate text-[#9fa1b1]">{projectVersionTitle(version)}</span>
        <span className="block text-[#828491]">{versionStatusLabel[version.status]}</span>
        <time dateTime={version.created_at} className="block text-[#828491]">{new Date(version.created_at).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" })}</time>
      </button>
    </li>)}</ol>
    {(hasOlder || error) && onLoadOlder && <button type="button" onClick={onLoadOlder} disabled={loadingOlder} data-testid="max-history-load-older" className="my-2 min-h-9 w-full rounded border border-[#2b2d32] px-1 text-[10px] text-[#9fa1b1] disabled:opacity-50">{loadingOlder ? "Загружаем…" : error ? "Повторить" : "Более ранние"}</button>}
  </nav>;
}
