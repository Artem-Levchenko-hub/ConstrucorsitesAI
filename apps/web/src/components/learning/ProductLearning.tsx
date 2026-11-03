"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { BookOpen, ChevronLeft, ChevronRight, X } from "lucide-react";
import { usePathname, useSearchParams } from "next/navigation";
import { learningForRoute, learningForSurface, placeLearningCard, type LearningDefinition, type LearningStep } from "@/lib/product-learning";
import "./product-learning.css";

const OPEN_EVENT = "yleum:learning:open";
const DIALOG_SELECTOR = '[role="dialog"],[role="alertdialog"],dialog[open]';
type Scope = "product" | "guide";
type Status = "started" | "dismissed" | "completed";
type Rect = { left: number; top: number; width: number; height: number };
const unavailableStorage = new Set<string>();
const storageKey = (user: string) => `yleum:learning:v1:${user}`;

function records(user: string): Record<string, Status> {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey(user)) ?? "null");
    return value?.version === 1 && value.tours && typeof value.tours === "object" ? value.tours : {};
  } catch { return {}; }
}
function remember(user: string, id: string, status: Status) {
  try { localStorage.setItem(storageKey(user), JSON.stringify({ version: 1, tours: { ...records(user), [id]: status } })); }
  catch { unavailableStorage.add(`${user}:${id}`); }
}
function targetFor(step: LearningStep, duringTour = false): HTMLElement | null {
  return [...document.querySelectorAll<HTMLElement>(step.selector)].find(element => {
    if (!element.isConnected || element.closest("[hidden],[inert]") || (!duringTour && element.closest('[aria-hidden="true"]')) || element.matches(":disabled")) return false;
    for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse" || style.opacity === "0") return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }) ?? null;
}
const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

function openLearning(trigger: HTMLElement, scope: Scope) {
  const surface = trigger.closest<HTMLElement>("[data-learning-context]")?.dataset.learningContext;
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: { trigger, scope, surface } }));
}

export function LearningButton({ label = "Обучение", className = "" }: { label?: string; className?: string }) {
  return <button type="button" className={`learning-launch ${className}`} onClick={event => openLearning(event.currentTarget, "product")}><BookOpen aria-hidden="true" size={16} /><span>{label}</span></button>;
}

function LearningHost({ definition, userKey, auto = false, scope = "product" }: { definition: LearningDefinition; userKey: string; auto?: boolean; scope?: Scope }) {
  const [session, setSession] = useState<{ definition: LearningDefinition; steps: LearningStep[]; index: number; returnFocus: HTMLElement | null } | null>(null);
  const begin = useCallback((trigger?: HTMLElement, activeDefinition = definition) => {
    const existing = document.querySelector<HTMLElement>(DIALOG_SELECTOR);
    if (existing && (!trigger || !existing.contains(trigger))) return false;
    const steps = activeDefinition.steps.filter(step => targetFor(step));
    if (!steps.length) return false;
    const returnFocus = trigger ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    remember(userKey, activeDefinition.id, "started");
    setSession({ definition: activeDefinition, steps, index: 0, returnFocus });
    return true;
  }, [definition, userKey]);

  useEffect(() => {
    const open = (event: Event) => {
      const request = (event as CustomEvent<{ trigger: HTMLElement; scope: Scope; surface?: string }>).detail;
      if (request?.scope === scope && !session) begin(request.trigger, request.surface ? learningForSurface(request.surface) ?? definition : definition);
    };
    window.addEventListener(OPEN_EVENT, open);
    return () => window.removeEventListener(OPEN_EVENT, open);
  }, [begin, definition, scope, session]);

  useEffect(() => {
    if (!auto || session || records(userKey)[definition.id] || unavailableStorage.has(`${userKey}:${definition.id}`)) return;
    let timer: ReturnType<typeof setTimeout>;
    const check = () => {
      if (document.visibilityState === "hidden" || document.querySelector(`${DIALOG_SELECTOR},[data-learning-ready="false"]`)) return;
      if (document.activeElement?.matches('input,textarea,select,[contenteditable="true"]')) return;
      begin();
    };
    const schedule = () => { clearTimeout(timer); timer = setTimeout(check, 600); };
    const observer = new MutationObserver(schedule);
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-learning-ready", "hidden"] });
    document.addEventListener("focusout", schedule);
    document.addEventListener("visibilitychange", schedule);
    schedule();
    return () => { clearTimeout(timer); observer.disconnect(); document.removeEventListener("focusout", schedule); document.removeEventListener("visibilitychange", schedule); };
  }, [auto, begin, definition.id, session, userKey]);

  const close = (status: Status = "dismissed") => { remember(userKey, session?.definition.id ?? definition.id, status); setSession(null); };
  return <Dialog.Root open={session !== null} onOpenChange={open => { if (!open) close(); }}>
    {session && <Dialog.Portal><LearningTour definition={session.definition} steps={session.steps} index={session.index}
      onIndex={index => setSession({ ...session, index })} onClose={close} returnFocus={session.returnFocus} /></Dialog.Portal>}
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
    target?.scrollIntoView?.({ behavior: reducedMotion() ? "auto" : "smooth", block: "center", inline: "nearest" });
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
  }, [current]);
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
      <label className="learning-select-label">Раздел обучения<select aria-label="Раздел обучения" value={index} onChange={event => onIndex(Number(event.target.value))}>{steps.map((step, stepIndex) => <option key={step.id} value={stepIndex}>{stepIndex + 1}. {step.title}</option>)}</select></label>
      <div className="learning-actions"><button type="button" disabled={index === 0} onClick={() => onIndex(index - 1)}><ChevronLeft size={16} aria-hidden="true" />Назад</button>{index < steps.length - 1 ? <button type="button" className="learning-next" onClick={() => onIndex(index + 1)}>Далее<ChevronRight size={16} aria-hidden="true" /></button> : <button type="button" className="learning-next" onClick={() => onClose("completed")}>Завершить</button>}</div>
    </Dialog.Content>
  </>;
}

export function ProductLearning({ userKey }: { userKey: string }) {
  const pathname = usePathname();
  const query = useSearchParams()?.toString() ?? "";
  const definition = useMemo(() => learningForRoute(pathname, query), [pathname, query]);
  if (!definition || definition.id === "guide") return null;
  return <LearningHost key={`${userKey}:${pathname}?${query}`} definition={definition} userKey={userKey} auto={pathname === "/max"} />;
}

export function GuideLearning() {
  const guide = learningForRoute("/max/guide", "")!;
  return <><button type="button" className="learning-launch learning-guide-launch" onClick={event => openLearning(event.currentTarget, "guide")}><BookOpen size={17} aria-hidden="true" />Перейти в режим обучения</button><LearningHost definition={guide} userKey="public-guide" scope="guide" /></>;
}
