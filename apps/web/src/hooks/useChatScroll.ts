import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

export function useChatScroll(ref: RefObject<HTMLDivElement | null>, content: string) {
  const followRef = useRef(true);
  const [following, setFollowing] = useState(true);
  const onScroll = useCallback(() => {
    const pane = ref.current;
    if (!pane) return;
    const atBottom = pane.scrollHeight - pane.clientHeight - pane.scrollTop < 72;
    followRef.current = atBottom;
    setFollowing(atBottom);
  }, [ref]);
  const scrollToLatest = useCallback(() => {
    followRef.current = true;
    setFollowing(true);
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [ref]);
  useEffect(() => {
    if (followRef.current && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [content, ref]);
  return { onScroll, scrollToLatest, following };
}
