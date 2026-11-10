"use client";

import { createContext, useContext, type ReactNode } from "react";
import { MotionConfig } from "framer-motion";
import { previewMotion as timing } from "./motion-preview";

const MotionPreviewContext = createContext({ reduced: true, slow: false });

export function MotionPreviewProvider({ reduced, slow, children }: {
  reduced: boolean; slow: boolean; children: ReactNode;
}) {
  return <MotionPreviewContext.Provider value={{ reduced, slow }}>
    <MotionConfig reducedMotion={reduced ? "always" : "never"}>{children}</MotionConfig>
  </MotionPreviewContext.Provider>;
}

export function usePreviewMotion() {
  const { reduced, slow } = useContext(MotionPreviewContext);
  const transition = (duration: number = timing.content, delay = 0) => ({
    duration: reduced ? timing.reduced : duration * (slow ? 2 : 1),
    delay: reduced ? 0 : delay * (slow ? 2 : 1),
    ease: timing.ease,
  });
  return {
    reduced,
    transition,
    instant: { duration: 0 },
    arrive: { opacity: 0, y: reduced ? 0 : timing.distance, scale: reduced ? 1 : timing.surfaceScale },
    leave: { opacity: 0, y: reduced ? 0 : -timing.distance, transition: transition(timing.exit) },
  };
}

export const milliseconds = (duration: number) => `${Math.round(duration * 1000)} мс`;
