"use client";

import { useId, useState, useSyncExternalStore, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, ChevronDown, SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { previewMotion as timing } from "./motion-preview";

const motionQuery = "(prefers-reduced-motion: reduce)";
const readMotionPreference = () => window.matchMedia?.(motionQuery).matches ?? true;
const serverMotionPreference = () => true;

function subscribeMotionPreference(onChange: () => void) {
  const media = window.matchMedia?.(motionQuery);
  media?.addEventListener("change", onChange);
  return () => media?.removeEventListener("change", onChange);
}

function Demo({ title, description, timing: duration, children }: {
  title: string; description: string; timing: string; children: ReactNode;
}) {
  return <article className="uk-motion-demo">
    <div className="uk-motion-copy"><h3>{title}</h3><p>{description}</p><code>{duration}</code></div>
    <div className="uk-motion-stage">{children}</div>
  </article>;
}

export function MotionLab() {
  // Subscribe directly: the installed motion hook snapshots the preference at mount.
  const systemReduced = useSyncExternalStore(subscribeMotionPreference, readMotionPreference, serverMotionPreference);
  const [reduce, setReduce] = useState(false);
  const reduced = Boolean(systemReduced || reduce);
  const [presses, setPresses] = useState(0);
  const [enabled, setEnabled] = useState(true);
  const [view, setView] = useState("Приложение");
  const [expanded, setExpanded] = useState(false);
  const [panel, setPanel] = useState(false);
  const [saved, setSaved] = useState(false);
  const id = useId();
  const transition = (duration: number) => ({ duration: reduced ? timing.reduced : duration, ease: timing.ease });

  return <div className="uk-lab" data-reduced={reduced}>
    <div className="uk-lab-intro">
      <p>Движение отвечает на действие. Небольшая дистанция, мягкое замедление, без отскока и бесконечных повторов.</p>
      <label className="uk-check"><input type="checkbox" checked={reduce} onChange={event => setReduce(event.target.checked)} /> Уменьшить движение</label>
      <p className="uk-caption" role="status">{systemReduced ? "В системе включено уменьшение движения — оно всегда имеет приоритет." : reduced ? "Только короткие переходы прозрачности. Положение меняется сразу." : "Каждый пример запускается только вашим действием."}</p>
    </div>
    <Demo title="Нажатие" description="Контрол едва сжимается и сразу отвечает. Без подъёма при наведении." timing="140 мс · scale 0.985">
      <motion.button className="uk-primary" whileTap={reduced ? undefined : { scale: timing.pressScale }} transition={transition(timing.control)} onClick={() => setPresses(value => value + 1)}>Сохранить черновик</motion.button>
      <span className="uk-caption uk-feedback" role="status">{presses ? `Сохранено · ${presses}` : "Нажмите и удерживайте"}</span>
    </Demo>
    <Demo title="Переключатель" description="Бегунок мягко догоняет новое состояние. Цвет и подпись подтверждают выбор." timing="140 мс · без пружины">
      <div className="uk-inline"><span id={`${id}-switch-label`}>Уведомления</span>
        <button type="button" className="uk-switch" role="switch" aria-checked={enabled} aria-labelledby={`${id}-switch-label`} onClick={() => setEnabled(value => !value)}>
          <motion.span animate={{ x: enabled ? 20 : 0 }} transition={reduced ? { duration: 0 } : transition(timing.control)} />
        </button>
      </div><span className="uk-caption" role="status">{enabled ? "Включены" : "Выключены"}</span>
    </Demo>
    <Demo title="Смена раздела" description="Подложка сохраняет связь между соседними состояниями." timing="180 мс · общий индикатор">
      <div className="uk-segments" role="group" aria-label="Предпросмотр">
        {["Приложение", "Настройки"].map(item => <button type="button" key={item} aria-pressed={view === item} onClick={() => setView(item)}>
          {view === item && <motion.span className="uk-segment-indicator" layoutId={reduced ? undefined : `${id}-segment`} transition={transition(timing.content)} />}
          <span>{item}</span>
        </button>)}
      </div><p className="uk-caption" role="status">{view === "Приложение" ? "Предпросмотр приложения" : "Параметры приложения"}</p>
    </Demo>
    <Demo title="Раскрытие" description="Дополнительная информация появляется рядом с её источником." timing="180 мс · раскрытие сетки">
      <div className="uk-disclosure">
        <button type="button" aria-expanded={expanded} aria-controls={`${id}-details`} onClick={() => setExpanded(value => !value)}>
          Что входит в публикацию?<ChevronDown size={16} className="uk-chevron" data-open={expanded} />
        </button>
        <div className="uk-disclosure-grid" data-open={expanded} id={`${id}-details`} inert={!expanded} aria-hidden={!expanded}>
          <div><p>Приложение, адрес и защищённое соединение. Это пример раскрытия, без публикации.</p></div>
        </div>
      </div>
    </Demo>
    <Demo title="Появление панели" description="Поверхность проявляется от точки действия и быстро уходит при закрытии." timing="240 / 120 мс · 4 px · scale 0.99">
      <Button variant="secondary" aria-expanded={panel} aria-controls={`${id}-panel`} onClick={() => setPanel(value => !value)}><SlidersHorizontal size={16} />{panel ? "Закрыть панель" : "Показать панель"}</Button>
      <div className="uk-panel-slot"><AnimatePresence initial={false}>
        {panel && <motion.div id={`${id}-panel`} role="region" aria-label="Параметры предпросмотра" className="uk-demo-panel"
          initial={{ opacity: 0, y: reduced ? 0 : timing.distance, scale: reduced ? 1 : timing.surfaceScale }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: reduced ? 0 : timing.distance, transition: transition(timing.exit) }} transition={transition(timing.surface)}>
          <strong>Предпросмотр готов</strong><p>Проверьте приложение перед запуском.</p>
        </motion.div>}
      </AnimatePresence></div>
    </Demo>
    <Demo title="Подтверждение" description="Результат заменяет действие на месте. Без вспышки и отдельного баннера." timing="180 мс · crossfade">
      <Button variant="secondary" className="uk-confirm" onClick={() => setSaved(value => !value)}>
        <AnimatePresence initial={false} mode="wait"><motion.span key={String(saved)} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={transition(timing.content)}>{saved ? <><Check size={16} /> Сохранено</> : "Сохранить пример"}</motion.span></AnimatePresence>
      </Button><span className="uk-caption" role="status">{saved ? "Готово. Нажмите, чтобы повторить." : "Изменения останутся только в этом примере."}</span>
    </Demo>
  </div>;
}
