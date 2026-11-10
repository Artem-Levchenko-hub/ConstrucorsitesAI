"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowUpRight, BookOpen, ChevronLeft, ChevronRight, X } from "lucide-react";
import { usePathname, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { guideChapterHref, type GuideLessonId } from "@/lib/guide-lessons";
import { learningForRoute, learningForSurface, placeLearningCard, type LearningDefinition, type LearningStep } from "@/lib/product-learning";
import { LEARNING_OPEN_EVENT, readLearningRecord, rememberLearning, requestLearning, useLearningGate, type LearningStatus } from "./LearningProvider";
import "./product-learning.css";

const DIALOG_SELECTOR = '[role="dialog"],[role="alertdialog"],dialog[open]';
type Scope = "product" | "guide";
type Status = LearningStatus;
type Rect = { left: number; top: number; width: number; height: number };
function targetFor(step: LearningStep, duringTour = false): HTMLElement | null {
  return [...document.querySelectorAll<HTMLElement>(step.selector)].find(element => {
    if (!element.isConnected || element.closest("[hidden]") || (!duringTour && element.closest('[aria-hidden="true"]')) || element.matches(":disabled")) return false;
    for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement) {
      if (parent.hasAttribute("inert") && (!duringTour || !parent.hasAttribute("data-learning-inert"))) return false;
      const style = getComputedStyle(parent);
      if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse" || style.opacity === "0") return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }) ?? null;
}
const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

export function LearningButton({ label = "Обучение", className = "" }: { label?: string; className?: string }) {
  const gate = useLearningGate();
  return (
    <Button type="button" variant="secondary" size="nav" className={`learning-launch ${className}`}
      onClick={event => requestLearning(gate?.userKey, event.currentTarget)}>
      <BookOpen aria-hidden="true" />
      <span>{label}</span>
    </Button>
  );
}

function LearningGuideLink({ lesson }: { lesson: GuideLessonId }) {
  return <a className="learning-guide-link" href={guideChapterHref(lesson)} target="_blank" rel="noopener noreferrer">
    <BookOpen size={17} aria-hidden="true" />
    <span>Читать руководство<span className="sr-only"> в новой вкладке</span></span>
    <ArrowUpRight size={14} aria-hidden="true" />
  </a>;
}

function blockBackground() {
  const changed = new Set<HTMLElement>();
  const apply = () => {
    for (const node of document.body.children) {
      if (!(node instanceof HTMLElement) || node.matches(".learning-card,.learning-overlay,[data-radix-focus-guard],script,style,link") || node.querySelector(".learning-card,.learning-overlay") || node.hasAttribute("inert")) continue;
      changed.add(node); node.setAttribute("inert", ""); node.setAttribute("data-learning-inert", "");
    }
  };
  const prevent = (event: Event) => {
    if (event.target instanceof Element && event.target.closest(".learning-card,[data-radix-focus-guard]")) return;
    event.preventDefault(); event.stopImmediatePropagation();
  };
  apply();
  const observer = new MutationObserver(apply); observer.observe(document.body, { childList: true });
  document.addEventListener("click", prevent, true); document.addEventListener("submit", prevent, true);
  return () => {
    observer.disconnect(); document.removeEventListener("click", prevent, true); document.removeEventListener("submit", prevent, true);
    for (const node of changed) { node.removeAttribute("inert"); node.removeAttribute("data-learning-inert"); }
  };
}

type Session = { definition: LearningDefinition; steps: LearningStep[]; index: number; returnFocus: HTMLElement | null };
function LearningHost({ definition, userKey, scope = "product" }: { definition: LearningDefinition; userKey: string; scope?: Scope }) {
  const [session, setSession] = useState<Session | null>(null);
  const token = useRef({});
  const gate = useLearningGate();
  const setActive = gate?.userKey === userKey ? gate.setActive : undefined;
  const isOpen = session !== null;
  useLayoutEffect(() => {
    if (!isOpen) return;
    const owner = token.current;
    setActive?.(owner, true);
    const restore = blockBackground();
    return () => { restore(); setActive?.(owner, false); };
  }, [isOpen, setActive]);
  const begin = useCallback((trigger?: HTMLElement, activeDefinition = definition) => {
    if (trigger && !trigger.isConnected) return false;
    const existing = document.querySelector<HTMLElement>(DIALOG_SELECTOR);
    if (existing && (!trigger || !existing.contains(trigger))) return false;
    const steps = activeDefinition.steps.filter(step => targetFor(step));
    if (!steps.length) return false;
    const returnFocus = trigger ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    const saved = readLearningRecord(userKey);
    const progress = saved.progress[activeDefinition.id];
    const index = saved.tours[activeDefinition.id] === "completed" ? 0 : Math.max(0, steps.findIndex(step => step.id === progress));
    rememberLearning(userKey, activeDefinition.id, "started", steps[index].id);
    setSession({ definition: activeDefinition, steps, index, returnFocus });
    return true;
  }, [definition, userKey]);

  useEffect(() => {
    const open = (event: Event) => {
      const request = (event as CustomEvent<{ trigger?: HTMLElement; scope: Scope; surface?: string; userKey?: string }>).detail;
      if (request?.scope === scope && (!request.userKey || request.userKey === userKey) && !session) begin(request.trigger, request.surface ? learningForSurface(request.surface) ?? definition : definition);
    };
    window.addEventListener(LEARNING_OPEN_EVENT, open);
    return () => window.removeEventListener(LEARNING_OPEN_EVENT, open);
  }, [begin, definition, scope, session, userKey]);

  const close = (status: Status = "dismissed") => { rememberLearning(userKey, session?.definition.id ?? definition.id, status, session?.steps[session.index]?.id); setSession(null); };
  const goTo = (index: number) => {
    if (!session || !session.steps[index]) return;
    rememberLearning(userKey, session.definition.id, "started", session.steps[index].id);
    setSession({ ...session, index });
  };
  return <Dialog.Root open={session !== null} onOpenChange={open => { if (!open) close(); }}>
    {session && <Dialog.Portal><LearningTour definition={session.definition} steps={session.steps} index={session.index}
      onIndex={goTo} onClose={close} returnFocus={session.returnFocus} /></Dialog.Portal>}
  </Dialog.Root>;
}

function LearningTour({ definition, steps, index, onIndex, onClose, returnFocus }: {
  definition: LearningDefinition; steps: LearningStep[]; index: number;
  onIndex: (index: number) => void; onClose: (status?: Status) => void; returnFocus: HTMLElement | null;
}) {
  const card = useRef<HTMLDivElement>(null);
  const [geometry, setGeometry] = useState<{ target: Rect | null; viewport: { width: number; height: number }; card: { width: number; height: number }; missing: boolean }>({ target: null, viewport: { width: 390, height: 780 }, card: { width: 358, height: 320 }, missing: false });
  const current = steps[index];
  useLayoutEffect(() => {
    const target = targetFor(current, true);
    const chapter = definition.id === "guide" ? target?.closest<HTMLElement>(".guide-chapter") : null;
    (chapter ?? target)?.scrollIntoView?.({ behavior: reducedMotion() ? "auto" : "smooth", block: chapter ? "start" : "center", inline: "nearest" });
    const measure = () => {
      const element = targetFor(current, true);
      const rect = element?.getBoundingClientRect();
      const width = window.innerWidth, height = window.innerHeight;
      const panel = card.current?.getBoundingClientRect();
      const left = Math.max(8, (rect?.left ?? 0) - 6), top = Math.max(8, (rect?.top ?? 0) - 6);
      const right = Math.min(width - 8, (rect?.right ?? 0) + 6), bottom = Math.min(height - 8, (rect?.bottom ?? 0) + 6);
      const next = { target: rect && right > left && bottom > top ? { left, top, width: right - left, height: bottom - top } : null,
        viewport: { width, height }, card: { width: Math.min(panel?.width ?? 358, width - 32), height: Math.min(panel?.height ?? 320, height - 32) }, missing: !element };
      setGeometry(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
    };
    measure();
    const observer = new ResizeObserver(measure);
    if (target) observer.observe(target);
    if (card.current) observer.observe(card.current);
    const mutations = new MutationObserver(measure);
    mutations.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "class", "style"] });
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => { observer.disconnect(); mutations.disconnect(); window.removeEventListener("resize", measure); window.removeEventListener("scroll", measure, true); };
  }, [current, definition.id]);
  const position = placeLearningCard(geometry.target, geometry.viewport, geometry.card);
  return <>
    <Dialog.Overlay className="learning-overlay" data-dimmed={!geometry.target || undefined}>
      {geometry.target && <div className="learning-spotlight" aria-hidden="true" style={geometry.target} />}
    </Dialog.Overlay>
    <Dialog.Content ref={card} className="learning-card" style={position} onPointerDownOutside={event => event.preventDefault()}
      onEscapeKeyDown={event => event.stopPropagation()}
      onCloseAutoFocus={event => { event.preventDefault(); if (returnFocus?.isConnected) returnFocus.focus(); }}>
      <div className="learning-card-top"><span><BookOpen size={15} aria-hidden="true" />{definition.title}</span><Dialog.Close asChild><button type="button" aria-label="Закрыть обучение" className="learning-close"><X size={18} aria-hidden="true" /></button></Dialog.Close></div>
      <div className="learning-progress" aria-hidden="true"><span style={{ width: `${(index + 1) / steps.length * 100}%` }} /></div>
      <p className="learning-counter" aria-live="polite">Шаг {index + 1} из {steps.length}</p>
      <Dialog.Title className="learning-title">{current.title}</Dialog.Title>
      <Dialog.Description className="learning-description">{current.body}</Dialog.Description>
      {geometry.missing && <p className="learning-missing" role="status">Элемент сейчас недоступен. Можно перейти к следующему шагу или закрыть обучение.</p>}
      <LearningGuideLink lesson={current.lesson ?? definition.lesson} />
      <div className="learning-actions"><button type="button" disabled={index === 0} onClick={() => onIndex(index - 1)}><ChevronLeft size={16} aria-hidden="true" />Назад</button>{index < steps.length - 1 ? <button type="button" className="learning-next" onClick={() => onIndex(index + 1)}>Далее<ChevronRight size={16} aria-hidden="true" /></button> : <button type="button" className="learning-next" onClick={() => onClose("completed")}>Завершить</button>}</div>
    </Dialog.Content>
  </>;
}

export function ProductLearning({ userKey }: { userKey: string }) {
  const pathname = usePathname();
  const query = useSearchParams()?.toString() ?? "";
  const definition = useMemo(() => learningForRoute(pathname, query), [pathname, query]);
  if (!definition || definition.id === "guide") return null;
  return <LearningHost key={`${userKey}:${pathname}?${query}`} definition={definition} userKey={userKey} />;
}

export function GuideLearning() {
  const guide = learningForRoute("/max/guide", "")!;
  return (
    <>
      <Button type="button" variant="secondary" size="nav" className="learning-launch px-4"
        onClick={event => requestLearning("public-guide", event.currentTarget, "guide")}>
        <BookOpen aria-hidden="true" />
        Перейти в режим обучения
      </Button>
      <LearningHost definition={guide} userKey="public-guide" scope="guide" />
    </>
  );
}
