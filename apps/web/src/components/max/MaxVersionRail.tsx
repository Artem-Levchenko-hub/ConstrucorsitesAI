"use client";

import { useRef, useState } from "react";
import { Check, Clock3 } from "lucide-react";
import type { ProjectVersion } from "@/lib/api/types";
import { versionImageUrl } from "@/lib/project-version";
import { maxHistoryState, maxVersionImage } from "@/lib/max-version-history";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export function MaxVersionRail({ versions, selectedVersionId, loading, error, hasOlder, loadingOlder, onLoadOlder, onSelect, failedImages, onImageError, onRetryImages, onPrepareRestoration, restorationEnabled, restorationBusy }: {
  versions: ProjectVersion[];
  selectedVersionId: string | null;
  loading: boolean;
  error?: boolean;
  hasOlder?: boolean;
  loadingOlder?: boolean;
  onLoadOlder?: () => void;
  onSelect: (versionId: string | null) => void;
  failedImages: ReadonlySet<string>;
  onImageError: (url: string) => void;
  onRetryImages: () => void;
  onPrepareRestoration?: (version: ProjectVersion) => void;
  restorationEnabled?: boolean;
  restorationBusy?: boolean;
}) {
  const [activityOpen, setActivityOpen] = useState(false);
  const activityTrigger = useRef<HTMLButtonElement>(null);
  const rail = useRef<HTMLElement>(null);
  const available = versions.filter((version) => {
    const image = maxVersionImage(version);
    return image && !failedImages.has(image.url);
  });
  const other = versions.filter((version) => !available.includes(version));
  return <nav ref={rail} tabIndex={-1} className="max-version-rail" aria-label="История версий" aria-busy={loading} data-testid="max-version-rail">
    <p className="max-version-rail-heading">Версии</p>
    <div className="max-projects-scroll max-version-rail-scroll">
      {loading && !versions.length ? <p role="status" className="max-version-rail-empty">Загружаем…</p> : !available.length && <p className="max-version-rail-empty">{error ? "История недоступна" : "Готовые снимки появятся здесь"}</p>}
      <ol className="max-version-items">{available.map((version) => {
        const image = maxVersionImage(version)!;
        const selected = selectedVersionId === version.id || (selectedVersionId === null && version.is_current);
        return <li key={version.id}>
          <button type="button" onClick={() => onSelect(version.id)} aria-pressed={selected} aria-label={`Версия ${version.number}${version.is_current ? ", текущая" : ""}`} title={`Версия ${version.number}${version.is_current ? " · текущая" : ""}`} data-testid={`max-version-${version.number}`} className="max-version-item">
            {/* Authenticated, immutable capture: preserve the API image URL. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img key={image.url} src={versionImageUrl(image.url)} alt="" loading="lazy" onError={() => onImageError(image.url)} />
            <span className="max-version-item-label"><span>v{version.number}</span>{version.is_current && <Check aria-hidden="true" className="size-3.5" />}</span>
          </button>
        </li>;
      })}</ol>
      {(hasOlder || error) && onLoadOlder && <Button variant="outline" size="sm" type="button" onClick={onLoadOlder} disabled={loadingOlder} data-testid="max-history-load-older" className="max-version-older">{loadingOlder ? "Загружаем…" : error ? "Повторить" : "Ранее"}</Button>}
    </div>
    {other.length > 0 && <Button ref={activityTrigger} type="button" variant="outline" size="sm" className="max-version-activity-trigger" onClick={() => setActivityOpen(true)} data-testid="max-history-activity-open" aria-haspopup="dialog" aria-label={`Подготовка и попытки: ${other.length}`}>
      <Clock3 aria-hidden="true" /><span>Попытки <strong>{other.length}</strong></span>
    </Button>}
    <Dialog open={activityOpen} onOpenChange={setActivityOpen}>
      <DialogContent data-product-shell data-max-editor className="max-editor-version-dialog max-history-activity" data-testid="max-history-activity" onCloseAutoFocus={(event) => { event.preventDefault(); (activityTrigger.current ?? rail.current)?.focus(); }}>
        <DialogHeader>
          <DialogTitle>Подготовка и попытки</DialogTitle>
          <DialogDescription>Здесь — незавершённые запросы и версии, снимки которых пока недоступны. Готовые снимки находятся в ленте.</DialogDescription>
        </DialogHeader>
        {other.length ? <ul>{other.map((version) => {
          const image = maxVersionImage(version);
          const state = maxHistoryState(version, !!image && failedImages.has(image.url));
          return <li key={version.id} data-testid={`max-history-event-${version.number}`}>
            <div className="max-history-event-heading"><strong>v{version.number}</strong><span className="max-history-status" data-tone={state.tone}>{state.label}</span></div>
            <p>{state.hint}</p>
            <details><summary>Подробнее о запросе</summary><time dateTime={version.created_at}>{new Date(version.created_at).toLocaleString("ru-RU")}</time><p className="max-history-original-prompt">{version.prompt_text || "Текст запроса не сохранился."}</p></details>
            {onPrepareRestoration && version.snapshot_id && <Button variant="outline"
              data-testid={`max-history-prepare-${version.id}`}
              disabled={!restorationEnabled || restorationBusy}
              onClick={() => { setActivityOpen(false); onPrepareRestoration(version); }}>
              Подготовить восстановление v{version.number}
            </Button>}
            {!!image && failedImages.has(image.url) && <Button variant="outline" size="sm" data-retry-image onClick={onRetryImages}>Повторить загрузку</Button>}
          </li>;
        })}</ul> : <p>Все доступные снимки — в ленте версий.</p>}
      </DialogContent>
    </Dialog>
  </nav>;
}
