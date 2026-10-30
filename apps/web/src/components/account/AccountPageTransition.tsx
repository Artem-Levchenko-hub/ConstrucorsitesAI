"use client";

import { useEffect, useRef, useSyncExternalStore, type ReactNode } from "react";
import { EASE_OUT } from "@/lib/motion";

function motionPreference() {
  return typeof window === "undefined" ? undefined : window.matchMedia?.("(prefers-reduced-motion: reduce)");
}

function subscribeMotionPreference(onChange: () => void) {
  const media = motionPreference();
  if (!media) return () => {};
  if (typeof media.addEventListener === "function") {
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }
  media.addListener(onChange);
  return () => media.removeListener(onChange);
}

const readMotionPreference = () => motionPreference()?.matches ?? true;
const serverMotionPreference = () => true;

export function AccountPageTransition({ children, transitionKey, className }: { children: ReactNode; transitionKey: string; className?: string }) {
  const surface = useRef<HTMLDivElement>(null);
  const previousKey = useRef(transitionKey);
  const reducedMotion = useSyncExternalStore(subscribeMotionPreference, readMotionPreference, serverMotionPreference);

  useEffect(() => {
    const changed = previousKey.current !== transitionKey;
    previousKey.current = transitionKey;
    const element = surface.current;
    if (!changed || reducedMotion !== false || typeof element?.animate !== "function") return;

    // Animate the current surface only: no remount, outgoing page or retained private content.
    const animation = element.animate([
      { opacity: .72, transform: "translateY(6px)" },
      { opacity: 1, transform: "translateY(0)" },
    ], { duration: 320, easing: `cubic-bezier(${EASE_OUT.join(",")})` });
    return () => animation.cancel();
  }, [transitionKey, reducedMotion]);

  return <div ref={surface} className={`account-page-transition${className ? ` ${className}` : ""}`}>{children}</div>;
}
