"use client";

import { useCallback, useRef, useState } from "react";
import { ApiError, postBlob } from "@/lib/api/client";

export type VoiceState = "idle" | "recording" | "transcribing";

/**
 * Voice prompt dictation. Records the mic with MediaRecorder, POSTs the blob to
 * `/api/transcribe` (→ gateway → proxyapi whisper, RU), and hands the text back
 * via `onText` — REVIEW-FIRST: the caller drops it into the prompt box, the user
 * edits + sends (a misheard word never auto-fires a build).
 *
 * Lazy mic permission (requested on first record), graceful when the browser
 * lacks getUserMedia/MediaRecorder (`supported=false` → caller hides the button).
 * Fail-soft: any capture/permission/transcription error surfaces as `error` and
 * resets to idle; the mic stream is always torn down.
 */
export function useVoiceInput(onText: (text: string) => void) {
  const [state, setState] = useState<VoiceState>("idle");
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const supported =
    typeof window !== "undefined" &&
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined";

  const stop = useCallback(() => {
    const rec = recorderRef.current;
    if (rec && rec.state !== "inactive") rec.stop();
  }, []);

  const start = useCallback(async () => {
    if (!supported) {
      setError("Браузер не поддерживает запись с микрофона");
      return;
    }
    setError(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("Нет доступа к микрофону — разрешите его в браузере");
      return;
    }

    chunksRef.current = [];
    const mime = MediaRecorder.isTypeSupported("audio/webm")
      ? "audio/webm"
      : MediaRecorder.isTypeSupported("audio/mp4")
        ? "audio/mp4"
        : "";
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    recorderRef.current = rec;

    rec.ondataavailable = (e) => {
      if (e.data.size) chunksRef.current.push(e.data);
    };
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunksRef.current, {
        type: rec.mimeType || "audio/webm",
      });
      if (!blob.size) {
        setState("idle");
        return;
      }
      setState("transcribing");
      try {
        const { text } = await postBlob<{ text: string }>(
          "/api/transcribe",
          blob,
          { timeoutMs: 90_000 },
        );
        const trimmed = text.trim();
        if (trimmed) onText(trimmed);
        else setError("Не расслышал — попробуйте ещё раз");
      } catch (e) {
        setError(
          e instanceof ApiError && e.status === 429
            ? "Слишком часто — подождите немного"
            : "Не удалось распознать речь",
        );
      } finally {
        setState("idle");
      }
    };

    rec.start();
    setState("recording");
  }, [supported, onText]);

  const toggle = useCallback(() => {
    if (state === "recording") stop();
    else if (state === "idle") void start();
  }, [state, start, stop]);

  return { state, error, supported, toggle } as const;
}
