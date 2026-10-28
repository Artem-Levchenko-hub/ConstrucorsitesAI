"use client";

import { useRef, useState } from "react";
import { findSupportAnswers, searchSupportAnswers, type SupportAnswer, type SupportTopic } from "@/lib/support-faq";

export type SupportExchange = {
  id: number;
  question: string;
  answers: readonly SupportAnswer[];
  resolution: "answer" | "choices" | "unknown";
  feedback: "yes" | "no" | null;
};
export const supportConversationLimit = 20;

/** Owned by the principal-scoped support session; retained on close, never persisted to storage. */
export function useSupportAssistant() {
  const [question, setQuestion] = useState("");
  const [topic, setTopic] = useState<SupportTopic | null>(null);
  const [browsing, setBrowsing] = useState(true);
  const [exchanges, setExchanges] = useState<SupportExchange[]>([]);
  const sequence = useRef(0);
  function ask(value: string, selected?: SupportAnswer) {
    const text = value.trim().slice(0, 500);
    if (!text) return;
    const exact = selected ? [selected] : findSupportAnswers(text);
    const answers = exact.length ? exact : searchSupportAnswers(text);
    const resolution = selected || exact.length === 1 ? "answer" : answers.length ? "choices" : "unknown";
    const exchange: SupportExchange = { id: ++sequence.current, question: text, answers, resolution, feedback: null };
    setExchanges(previous => [...previous.slice(-(supportConversationLimit - 1)), exchange]);
    setQuestion(""); setBrowsing(false);
  }
  function feedback(id: number, value: "yes" | "no") {
    setExchanges(previous => previous.map(exchange => exchange.id === id && exchange.resolution === "answer" ? { ...exchange, feedback: value } : exchange));
  }
  function browse(value: SupportTopic | null = null) { setTopic(value); setBrowsing(true); }
  function restart() { setQuestion(""); setTopic(null); setExchanges([]); setBrowsing(true); }
  return { question, setQuestion, topic, browsing, setBrowsing, exchanges, ask, feedback, browse, restart };
}

export type SupportAssistantSession = ReturnType<typeof useSupportAssistant>;
