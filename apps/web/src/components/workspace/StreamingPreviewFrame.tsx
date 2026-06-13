"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  collectStreamingFilesPartial,
  extractStreamingBody,
} from "@/lib/parse-assistant";
import { buildBootstrap } from "@/lib/streaming-preview-bootstrap";
import type { StreamBrief } from "@/lib/api/types";

export type StreamingDevice = "mobile" | "tablet" | "desktop";

const DEVICE_WIDTH: Record<StreamingDevice, string> = {
  mobile: "390px",
  tablet: "768px",
  desktop: "100%",
};

/**
 * Долгоживущий iframe, который получает дельты через postMessage и патчит
 * DOM через morphdom внутри (см. streaming-preview-bootstrap.ts). На каждое
 * изменение `content` пере-парсим, собираем bodyHtml + cssText, шлём в iframe
 * c debounce 150ms.
 *
 * Memo по `content.length` — content монотонно дописывается, длина растёт,
 * это дешёвый proxy чтоб не вызывать parseAssistantContent дважды для тех
 * же данных при сторонних re-render-ах.
 */
export function StreamingPreviewFrame({
  content,
  device,
  projectId,
  messageId,
}: {
  content: string;
  device: StreamingDevice;
  projectId: string;
  messageId: string;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);

  // Live image drop-in: resolved images for THIS message, populated by
  // usePromptStream on `image.resolved` events. Client-only cache (no fetch);
  // same pattern as the multipass ["passes"] cache (PassProgressBar).
  const { data: streamImages } = useQuery<{ idx: number; url: string }[]>({
    queryKey: ["stream-images", projectId, messageId],
    queryFn: () => [],
    enabled: false,
    initialData: [],
  });
  // Track which (idx,url) we've already pushed so re-renders don't replay the
  // settle animation; reset when the message changes (new generation).
  const postedRef = useRef<Set<string>>(new Set());

  // V3.10a — the art-director brief for THIS message, populated by
  // usePromptStream on the `omnia:brief` event (same client-only cache pattern
  // as streamImages). Forwarded into the iframe so the live render can narrate
  // the design reasoning (palette/fonts/sections) as the page builds (V3.10).
  const { data: streamBrief } = useQuery<StreamBrief | null>({
    queryKey: ["stream-brief", projectId, messageId],
    queryFn: () => null,
    enabled: false,
    initialData: null,
  });

  // Bootstrap srcDoc with the canonical omnia-kit.css linked from the API
  // origin (styling parity with the committed /p/<slug>). Memoised empty-dep
  // so srcDoc stays STABLE — the iframe is long-lived; a changing srcDoc would
  // reload it and kill the in-flight morphdom session.
  const bootstrap = useMemo(
    () => buildBootstrap(process.env.NEXT_PUBLIC_API_URL ?? ""),
    [],
  );

  const { bodyHtml, cssText } = useMemo(() => {
    const body = extractStreamingBody(content) ?? "";
    const files = collectStreamingFilesPartial(content);
    const css = Object.entries(files)
      .filter(([path]) => path.endsWith(".css"))
      .map(([, body]) => body)
      .join("\n\n");
    return { bodyHtml: body, cssText: css };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content.length]);

  // Слушаем сигнал готовности bootstrap-а — без него postMessage сработает
  // вхолостую (listener ещё не зарегистрирован, особенно на первом тике).
  useEffect(() => {
    function onMessage(e: MessageEvent) {
      if (e.source !== iframeRef.current?.contentWindow) return;
      if ((e.data as { type?: string })?.type === "omnia:ready") {
        setReady(true);
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // Debounce 150ms — естественный coalesce для llm.chunk потока.
  useEffect(() => {
    if (!ready) return;
    const win = iframeRef.current?.contentWindow;
    if (!win) return;
    const id = window.setTimeout(() => {
      win.postMessage(
        { type: "omnia:render", bodyHtml, cssText },
        "*",
      );
    }, 150);
    return () => window.clearTimeout(id);
  }, [ready, bodyHtml, cssText]);

  // Honest status pings into the placeholder. The bootstrap iframe listens
  // for `omnia:status` and updates its label without tearing the skeleton
  // down. Without this the user stares at a frozen "AI пишет ответ" even
  // when the backend has switched to a fallback model mid-stream.
  useEffect(() => {
    if (!ready) return;
    const win = iframeRef.current?.contentWindow;
    if (!win) return;
    const chars = content.length;
    // If we have any HTML content yet, the bootstrap script will tear down
    // the placeholder on the next render — no point updating its label.
    if (bodyHtml.length > 0) return;
    let status: string;
    if (chars === 0) {
      status = "AI читает контекст";
    } else if (content.includes("Переключаюсь на")) {
      // Match the inline notice the api sends as an llm.chunk delta when it
      // falls back to a different model — see _process_prompt empty_fallback.
      status = "Запасная модель пишет ответ";
    } else if (chars < 80) {
      status = "Модель думает";
    } else {
      status = "AI генерирует код";
    }
    win.postMessage({ type: "omnia:status", text: status }, "*");
  }, [ready, content, bodyHtml]);

  // New generation: forget what we've posted and clear the iframe's image map
  // so a previous build's photos can't bleed into the new frames (idx reuse).
  useEffect(() => {
    postedRef.current = new Set();
    iframeRef.current?.contentWindow?.postMessage(
      { type: "omnia:images-reset" },
      "*",
    );
  }, [messageId]);

  // Push each newly-resolved image into its frame. The iframe animates it
  // settling in (blur+scale → sharp). Only NEW (idx,url) pairs are posted so a
  // re-render never replays the animation.
  useEffect(() => {
    if (!ready) return;
    const win = iframeRef.current?.contentWindow;
    if (!win) return;
    for (const img of streamImages ?? []) {
      const key = `${img.idx}:${img.url}`;
      if (postedRef.current.has(key)) continue;
      postedRef.current.add(key);
      win.postMessage({ type: "omnia:image", idx: img.idx, url: img.url }, "*");
    }
  }, [ready, streamImages]);

  // V3.10a — forward the art-director brief into the iframe once both the brief
  // has arrived and the bootstrap is ready. The bootstrap stores it on
  // window.__omniaBrief; the visible narration is built on top in V3.10.
  useEffect(() => {
    if (!ready || !streamBrief) return;
    iframeRef.current?.contentWindow?.postMessage(
      { type: "omnia:brief", brief: streamBrief },
      "*",
    );
  }, [ready, streamBrief]);

  return (
    <motion.iframe
      ref={iframeRef}
      key="streaming"
      srcDoc={bootstrap}
      title="Preview (streaming)"
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-pointer-lock"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2 }}
      style={{ width: DEVICE_WIDTH[device], maxWidth: "100%" }}
      className="h-full bg-white border-0 mx-auto shadow-xl"
    />
  );
}
