import { useCallback, useLayoutEffect, useRef, useState, type RefObject } from "react";

type ChatScrollOptions = {
  contentRef?: RefObject<HTMLDivElement | null>;
  footerRef?: RefObject<HTMLDivElement | null>;
  scopeKey?: string;
};

export function useChatScroll(
  ref: RefObject<HTMLDivElement | null>,
  content: string,
  { contentRef, footerRef, scopeKey }: ChatScrollOptions = {},
) {
  const followRef = useRef(true);
  const scheduleFollowRef = useRef<(() => void) | null>(null);
  const [following, setFollowing] = useState(true);
  const [followingScope, setFollowingScope] = useState(scopeKey);
  // Reading history in one conversation must not carry into another project.
  if (followingScope !== scopeKey) {
    setFollowingScope(scopeKey);
    setFollowing(true);
  }
  const scrollToBottom = useCallback(() => {
    const pane = ref.current;
    if (pane) pane.scrollTop = pane.scrollHeight;
  }, [ref]);
  const onScroll = useCallback(() => {
    const pane = ref.current;
    if (!pane) return;
    const atBottom = pane.scrollHeight - pane.clientHeight - pane.scrollTop < 72;
    followRef.current = atBottom;
    setFollowing(atBottom);
  }, [ref]);
  const pauseFollowing = useCallback(() => {
    // Reading intent precedes layout growth and any already queued frame.
    followRef.current = false;
    setFollowing(false);
  }, []);
  const scrollToLatest = useCallback(() => {
    followRef.current = true;
    setFollowing(true);
    scrollToBottom();
  }, [scrollToBottom]);

  useLayoutEffect(() => {
    let active = true;
    let frame: number | null = null;
    followRef.current = true;
    const scheduleFollow = () => {
      if (!active || !followRef.current || frame !== null) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        if (active && followRef.current) scrollToBottom();
      });
    };
    scheduleFollowRef.current = scheduleFollow;
    const pane = ref.current;
    const footer = footerRef?.current;
    const updateFooterInset = () => {
      if (pane && footer) pane.style.setProperty("--max-chat-footer-height", `${footer.offsetHeight}px`);
    };
    updateFooterInset();
    // AgentTranscript can grow from its own query cache or animation without
    // changing the parent message. Measure the rendered content, not that token.
    const element = contentRef?.current;
    const observer = (element || footer) && typeof ResizeObserver !== "undefined"
      ? new ResizeObserver((entries) => {
          if (!active) return;
          if (entries.some((entry) => entry.target === footer)) updateFooterInset();
          scheduleFollow();
        }) : null;
    if (element) observer?.observe(element);
    if (footer) observer?.observe(footer, { box: "border-box" });
    scheduleFollow();
    return () => {
      active = false;
      observer?.disconnect();
      if (footer) pane?.style.removeProperty("--max-chat-footer-height");
      if (frame !== null) cancelAnimationFrame(frame);
      scheduleFollowRef.current = null;
    };
  }, [ref, contentRef, footerRef, scopeKey, scrollToBottom]);

  // Retain the message-text fallback for initial content and older browsers.
  useLayoutEffect(() => { scheduleFollowRef.current?.(); }, [content]);
  return { onScroll, pauseFollowing, scrollToLatest, following };
}
