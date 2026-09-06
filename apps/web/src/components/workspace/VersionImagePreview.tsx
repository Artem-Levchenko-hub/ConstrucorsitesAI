"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { ProjectVersion, VersionPreviewImage } from "@/lib/api/types";
import { versionStatusLabel, versionImageUrl } from "@/lib/project-version";

/** Image-only history. Loading an artifact never calls a render/runtime endpoint. */
export function VersionImagePreview({ identity, label, images, previewStatus, status, previous, next, onPrevious, onNext, showControls = true }: {
  identity: string;
  label: string;
  images: VersionPreviewImage[];
  previewStatus: ProjectVersion["preview_status"];
  status?: ProjectVersion["status"];
  previous?: VersionPreviewImage;
  next?: VersionPreviewImage;
  onPrevious?: () => void;
  onNext?: () => void;
  showControls?: boolean;
}) {
  useEffect(() => {
    const preload = [previous?.url, next?.url].filter(Boolean).map((url) => {
      const img = new Image(); img.src = versionImageUrl(url!); return img;
    });
    return () => { for (const img of preload) img.onload = img.onerror = null; };
  }, [previous?.url, next?.url]);
  return <ImagePage key={identity} {...{ label, images, previewStatus, status, onPrevious, onNext, showControls }} />;
}

function ImagePage({ label, images, previewStatus, status, onPrevious, onNext, showControls }: Omit<Parameters<typeof VersionImagePreview>[0], "identity" | "previous" | "next">) {
  const touch = useRef<{ x: number; y: number } | null>(null);
  const [screen, setScreen] = useState(0);
  const currentImage = images[screen] ?? images[0];
  return (
    <section className="flex h-full w-full min-h-0 flex-col bg-[#191b20] text-white" data-testid="history-image-viewer" aria-label={label} tabIndex={0}
      style={{ touchAction: "pan-y" }}
      onKeyDown={(event) => {
        if (event.target instanceof HTMLSelectElement || event.altKey || event.metaKey || event.ctrlKey) return;
        if (event.key === "ArrowLeft" && onPrevious) { event.preventDefault(); onPrevious(); }
        if (event.key === "ArrowRight" && onNext) { event.preventDefault(); onNext(); }
      }}
      onTouchStart={(event) => { const point = event.touches[0]; touch.current = point ? { x: point.clientX, y: point.clientY } : null; }}
      onTouchCancel={() => { touch.current = null; }}
      onTouchEnd={(event) => {
        const start = touch.current; touch.current = null;
        const end = event.changedTouches[0]; if (!start || !end) return;
        const dx = end.clientX - start.x, dy = end.clientY - start.y;
        if (Math.abs(dx) >= 60 && Math.abs(dx) > Math.abs(dy) * 1.5) { if (dx < 0) onNext?.(); else onPrevious?.(); }
      }}>
      {showControls && <div className="flex shrink-0 items-center gap-2 border-b border-white/10 px-3 py-2 text-[11px]">
        <button type="button" aria-label="Предыдущая версия" disabled={!onPrevious} onClick={onPrevious} className="grid size-9 shrink-0 place-items-center rounded disabled:opacity-25"><ChevronLeft className="size-4" /></button>
        <div className="min-w-0 flex-1 text-center"><p className="font-semibold">{label} · только просмотр</p>{status && <p className="text-[#9fa1b1]">{versionStatusLabel[status]}</p>}</div>
        <button type="button" aria-label="Следующая версия" disabled={!onNext} onClick={onNext} className="grid size-9 shrink-0 place-items-center rounded disabled:opacity-25"><ChevronRight className="size-4" /></button>
      </div>}
      {showControls && images.length > 1 && <select className="mx-3 my-2 rounded bg-[#2b2d32] p-2 text-xs" aria-label="Экран версии" value={screen} onChange={(event) => setScreen(Number(event.target.value))}>{images.map((image, index) => <option key={`${image.route}:${index}`} value={index}>{image.route || `Экран ${index + 1}`}{image.width > 0 ? ` · ${image.width}px` : ""}</option>)}</select>}
      {showControls && currentImage?.reconstructed && <p className="shrink-0 px-3 py-2 text-center text-[10px] text-[#9fa1b1]">Восстановлено из кода · данные для предпросмотра</p>}
      {currentImage ? <ArtifactImage key={currentImage.url} image={currentImage} label={label} /> : <div className="flex flex-1 items-center justify-center p-8 text-center text-sm text-[#9fa1b1]" role="status">{previewStatus === "pending" ? "Изображение готовится" : previewStatus === "failed" ? "Изображение не сохранилось" : "Для этой версии нет изображения"}</div>}
    </section>
  );
}

function ArtifactImage({ image, label }: { image: VersionPreviewImage; label: string }) {
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const [attempt, setAttempt] = useState(0);
  return <div className="min-h-0 flex-1 overscroll-contain" style={{ overflowY: "auto" }} data-testid="history-image-scroll" aria-busy={state === "loading"}>
    {state === "loading" && <p role="status" className="p-3 text-center text-xs text-[#9fa1b1]">Загружаем изображение…</p>}
    {state === "failed" ? <div className="p-8 text-center text-sm"><p role="status">Не удалось загрузить изображение</p><button type="button" className="mt-3 underline" onClick={() => { setState("loading"); setAttempt((n) => n + 1); }}>Повторить загрузку</button></div> : (
      // Immutable server capture, rendered at its natural aspect ratio.
      // eslint-disable-next-line @next/next/no-img-element
      <img key={attempt} src={versionImageUrl(image.url)} width={image.width || undefined} height={image.height || undefined} alt={`Снимок: ${label}`} data-testid="history-image" style={{ width: "100%", height: "auto", display: "block" }} onLoad={() => setState("ready")} onError={() => setState("failed")} />
    )}
  </div>;
}
