"use client";
import { useEffect, useState } from "react";

/** Keep waiting/deadline labels current while a conversation remains open. */
export function useSupportClock() {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}
