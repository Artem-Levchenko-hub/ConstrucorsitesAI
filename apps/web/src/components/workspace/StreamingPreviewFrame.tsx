"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  collectStreamingFilesPartial,
  extractStreamingBody,
} from "@/lib/parse-assistant";
import { BOOTSTRAP_HTML } from "@/lib/streaming-preview-bootstrap";

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
}: {
  content: string;
  device: StreamingDevice;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);

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

  return (
    <motion.iframe
      ref={iframeRef}
      key="streaming"
      srcDoc={BOOTSTRAP_HTML}
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
