"use client";

import { useId, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { HelpCircle, Menu, X } from "lucide-react";
import { MotionDemo } from "./MotionDemo";
import { milliseconds, usePreviewMotion } from "./MotionPreviewContext";
import { previewMotion as timing } from "./motion-preview";

function PressExample() {
  const { reduced, transition } = usePreviewMotion();
  const [presses, setPresses] = useState(0);
  return <MotionDemo title="Нажатие" description="Кнопка ощутимо, но мягко сжимается под пальцем и возвращается на место." detail={`${milliseconds(timing.control)} · scale ${timing.pressScale}`}>
    <motion.button type="button" className="uk-primary" whileTap={reduced ? undefined : { scale: timing.pressScale }} transition={transition(timing.control)} onClick={() => setPresses(value => value + 1)}>Сохранить черновик</motion.button>
    <span className="uk-caption" role="status">{presses ? `Сохранено · ${presses}` : "Нажмите и удерживайте"}</span>
  </MotionDemo>;
}

function SwitchExample() {
  const { reduced, transition, instant } = usePreviewMotion();
  const [enabled, setEnabled] = useState(true);
  const id = useId();
  return <MotionDemo title="Переключатель" description="Бегунок скользит между состояниями. Цвет и подпись подтверждают выбор." detail={`${milliseconds(timing.control)} · 20 px`}>
    <div className="uk-inline"><span id={id}>Уведомления</span>
      <button type="button" className="uk-switch" role="switch" aria-checked={enabled} aria-labelledby={id} onClick={() => setEnabled(value => !value)}>
        <motion.span initial={false} animate={{ x: enabled ? 20 : 0 }} transition={reduced ? instant : transition(timing.control)} />
      </button>
    </div><span className="uk-caption" role="status">{enabled ? "Включены" : "Выключены"}</span>
  </MotionDemo>;
}

function SegmentsExample() {
  const { reduced, transition } = usePreviewMotion();
  const [view, setView] = useState("Приложение");
  const id = useId();
  return <MotionDemo title="Смена раздела" description="Подложка перетекает к выбранному разделу, сохраняя связь между состояниями." detail={`${milliseconds(timing.content)} · общий индикатор`}>
    <div className="uk-segments" role="group" aria-label="Предпросмотр">
      {["Приложение", "Настройки"].map(item => <button type="button" key={item} aria-pressed={view === item} onClick={() => setView(item)}>
        {view === item && <motion.span className="uk-segment-indicator" layoutId={reduced ? undefined : `${id}-segment`} transition={transition()} />}
        <span>{item}</span>
      </button>)}
    </div><p className="uk-caption" role="status">{view === "Приложение" ? "Предпросмотр приложения" : "Параметры приложения"}</p>
  </MotionDemo>;
}

function IconExample() {
  const { reduced, transition } = usePreviewMotion();
  const [open, setOpen] = useState(false);
  return <MotionDemo title="Смена иконки" description="Меню превращается в крестик. Короткий поворот помогает прочитать новое действие." detail={`${milliseconds(timing.content)} · поворот 45°`}>
    <button type="button" className="uk-icon-button" aria-label={open ? "Закрыть пример меню" : "Открыть пример меню"} aria-pressed={open} onClick={() => setOpen(value => !value)}>
      <AnimatePresence initial={false} mode="wait">
        <motion.span key={String(open)} initial={{ opacity: 0, rotate: reduced ? 0 : -45, scale: reduced ? 1 : .85 }} animate={{ opacity: 1, rotate: 0, scale: 1 }} exit={{ opacity: 0, rotate: reduced ? 0 : 45 }} transition={transition(timing.control)}>
          {open ? <X size={24} /> : <Menu size={24} />}
        </motion.span>
      </AnimatePresence>
    </button><span className="uk-caption" role="status">{open ? "Нажмите, чтобы закрыть" : "Нажмите, чтобы открыть"}</span>
  </MotionDemo>;
}

function CheckboxExample() {
  const { reduced, transition, instant } = usePreviewMotion();
  const [checked, setChecked] = useState(false);
  return <MotionDemo title="Галочка выбора" description="Знак рисуется в момент выбора. Небольшое изменение масштаба подтверждает нажатие." detail={`${milliseconds(timing.content)} · прорисовка контура`}>
    <button type="button" role="checkbox" aria-checked={checked} className="uk-checkbox-button" onClick={() => setChecked(value => !value)}>
      <motion.span className="uk-checkbox-mark" animate={{ scale: checked && !reduced ? 1.08 : 1 }} transition={reduced ? instant : transition(timing.control)}>
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true">
          <motion.path d="m5 12 4 4L19 6" strokeLinecap="round" strokeLinejoin="round" initial={false} animate={{ pathLength: checked ? 1 : 0, opacity: checked ? 1 : 0 }} transition={reduced ? instant : transition()} />
        </svg>
      </motion.span>Получать новости проекта
    </button><span className="uk-caption" role="status">{checked ? "Выбрано" : "Не выбрано"}</span>
  </MotionDemo>;
}

function TooltipExample() {
  const { reduced, transition } = usePreviewMotion();
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const id = useId();
  const show = (hovered || focused) && !dismissed;
  return <MotionDemo title="Подсказка" description="Подсказка появляется рядом с контролом при наведении или фокусе. Escape закрывает её." detail={`${milliseconds(timing.control)} · 6 px`}>
    <div className="uk-tooltip-anchor" onMouseEnter={() => { setHovered(true); setDismissed(false); }} onMouseLeave={() => setHovered(false)}>
      <button type="button" className="uk-help-button" aria-describedby={show ? id : undefined} onFocus={() => { setFocused(true); setDismissed(false); }} onBlur={() => setFocused(false)} onClick={() => setDismissed(false)} onKeyDown={event => { if (event.key === "Escape") { setDismissed(true); event.stopPropagation(); } }}>
        <HelpCircle size={20} />Как это работает
      </button>
      <AnimatePresence initial={false}>{show && <motion.div role="tooltip" id={id} className="uk-tooltip" initial={{ opacity: 0, y: reduced ? 0 : 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={transition(timing.control)}>Изменения сохраняются в черновике.</motion.div>}</AnimatePresence>
    </div><span className="uk-caption">Наведите курсор или перейдите клавишей Tab.</span>
  </MotionDemo>;
}

export function ControlMotionExamples() {
  return <><PressExample /><SwitchExample /><SegmentsExample /><IconExample /><CheckboxExample /><TooltipExample /></>;
}
