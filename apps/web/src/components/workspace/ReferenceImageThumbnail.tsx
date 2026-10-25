"use client";

import { useEffect, useState } from "react";
import { referenceImageUrl } from "@/lib/api/reference-images";
import type { ReferenceImage } from "@/lib/api/types";

export function ReferenceImageThumbnail({ projectId, image }: { projectId: string; image: ReferenceImage }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); let objectUrl: string | undefined;
    fetch(referenceImageUrl(projectId, image.id), { credentials: "include", signal: controller.signal })
      .then(async (response) => { if (!response.ok) throw new Error("unavailable"); return response.blob(); })
      .then((blob) => { if (controller.signal.aborted) return; objectUrl = URL.createObjectURL(blob); setSrc(objectUrl); })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [projectId, image.id]);
  return <div className="w-24 overflow-hidden rounded-lg border border-border-subtle">
    {src ? <a href={src} target="_blank" rel="noreferrer" aria-label={`Открыть ${image.filename}`}>
      {/* Private authenticated blobs must not pass through the image optimizer. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={image.filename} className="h-20 w-full object-cover" />
    </a> : <span className="flex h-20 items-center px-2 text-xs" role={failed ? "status" : undefined}>{failed ? "Изображение недоступно" : "Загрузка…"}</span>}
    <p className="truncate px-1.5 py-1 text-xs" title={image.filename}>{image.filename}</p>
  </div>;
}
