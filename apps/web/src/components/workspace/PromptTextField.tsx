"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ComponentPropsWithoutRef, type RefObject } from "react";
import { createPortal } from "react-dom";

const narrow = "(max-width: 1023px)";
const onServer = () => false;
const isNarrow = () => window.matchMedia?.(narrow).matches === true;
function subscribeWidth(update: () => void) {
  const media = window.matchMedia?.(narrow);
  media?.addEventListener?.("change", update);
  return () => media?.removeEventListener?.("change", update);
}

// WebKit groups editable fields in one document for its form navigation bar.
// Keep the iOS composer in a document with just one field. React still owns its
// value, selection, paste and keyboard handlers, using the same DOM ref.
const fieldDocument = `<!doctype html><html lang="ru"><head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html,body{margin:0;overflow:hidden;background:transparent;-webkit-text-size-adjust:100%}
    textarea{box-sizing:border-box;display:block;width:100%;min-height:48px;margin:0;border:0;
      padding:13px 4px 13px 14px;resize:none;appearance:none;background:transparent;color:#19253a;
      font:400 16px/22px system-ui,-apple-system,sans-serif;outline:none;overflow-y:auto}
    textarea::placeholder{color:#637082;opacity:1}
  </style></head><body></body></html>`;

export function PromptTextField({ inputRef, compact, maxComposer, mobilePlaceholder, ...props }: ComponentPropsWithoutRef<"textarea"> & {
  inputRef: RefObject<HTMLTextAreaElement | null>;
  compact: boolean;
  maxComposer: boolean;
  mobilePlaceholder?: string;
}) {
  const mobile = useSyncExternalStore(subscribeWidth, isNarrow, onServer);
  const isolated = maxComposer && mobile && (/iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1));
  const frame = useRef<HTMLIFrameElement>(null);
  const [body, setBody] = useState<HTMLElement | null>(null);

  const resize = useCallback(() => {
    const input = inputRef.current;
    if (!input) return;
    const visibleHeight = window.visualViewport?.height || window.innerHeight;
    const mobileLimit = Math.max(48, Math.min(144, Math.floor(visibleHeight * .28)));
    const limit = compact ? (mobile ? mobileLimit : 144) : 200;
    input.style.height = "0px";
    const height = Math.min(input.scrollHeight, limit);
    input.style.height = height + "px";
    if (isolated && frame.current) frame.current.style.height = Math.max(48, height) + "px";
  }, [inputRef, isolated, compact, mobile]);
  useLayoutEffect(resize, [resize, body, props.value]);
  useEffect(() => {
    window.addEventListener("resize", resize);
    window.visualViewport?.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      window.visualViewport?.removeEventListener("resize", resize);
    };
  }, [resize]);

  const field = <textarea {...props} ref={inputRef}
    placeholder={mobile && mobilePlaceholder ? mobilePlaceholder : props.placeholder} />;
  if (!isolated) return field;
  return <><iframe ref={frame} className="prompt-input-ios-frame" title="Поле ввода сообщения"
    height={48} srcDoc={fieldDocument} onLoad={event => setBody(event.currentTarget.contentDocument?.body ?? null)} />
    {body && createPortal(field, body)}</>;
}
