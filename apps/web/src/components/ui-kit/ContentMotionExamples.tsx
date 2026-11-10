"use client";

import { useId, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowLeft, ArrowRight, ChevronDown, Plus, RotateCcw, SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MotionDemo } from "./MotionDemo";
import { milliseconds, usePreviewMotion } from "./MotionPreviewContext";
import { previewMotion as timing } from "./motion-preview";

function DisclosureExample() {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  return <MotionDemo title="Раскрытие" description="Контент плавно раздвигает соседние строки, а стрелка показывает направление." detail={`${milliseconds(timing.content)} · раскрытие сетки`}>
    <div className="uk-disclosure">
      <button type="button" aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded(value => !value)}>Что входит в публикацию?<ChevronDown size={16} className="uk-chevron" data-open={expanded} /></button>
      <div className="uk-disclosure-grid" data-open={expanded} id={id} inert={!expanded} aria-hidden={!expanded}><div><p>Приложение, адрес и защищённое соединение. Это пример раскрытия, без публикации.</p></div></div>
    </div>
  </MotionDemo>;
}

function PanelExample() {
  const { transition, arrive, leave } = usePreviewMotion();
  const [panel, setPanel] = useState(false);
  const id = useId();
  return <MotionDemo title="Появление панели" description="Поверхность приближается к своему месту с мягким замедлением. Закрытие короче открытия." detail={`${milliseconds(timing.surface)} / ${milliseconds(timing.exit)} · ${timing.distance} px · scale ${timing.surfaceScale}`}>
    <Button variant="secondary" aria-expanded={panel} aria-controls={id} onClick={() => setPanel(value => !value)}><SlidersHorizontal size={16} />{panel ? "Закрыть панель" : "Показать панель"}</Button>
    <div className="uk-panel-slot"><AnimatePresence initial={false}>{panel && <motion.div id={id} role="region" aria-label="Параметры предпросмотра" className="uk-demo-panel" initial={arrive} animate={{ opacity: 1, y: 0, scale: 1 }} exit={leave} transition={transition(timing.surface)}>
      <strong>Предпросмотр готов</strong><p>Проверьте приложение перед запуском.</p>
    </motion.div>}</AnimatePresence></div>
  </MotionDemo>;
}

const listItems = ["Каталог товаров", "Запись на услуги", "Клуб клиентов"];

function ListExample() {
  const { reduced, transition } = usePreviewMotion();
  const [items, setItems] = useState([listItems[0]]);
  const complete = items.length === listItems.length;
  return <MotionDemo title="Живой список" description="Новые строки занимают своё место, а соседние мягко сдвигаются. Порядок можно поменять." detail={`${milliseconds(timing.content)} · layout-переход`}>
    <div className="uk-list-controls">
      <Button size="sm" variant="secondary" onClick={() => setItems(value => complete ? [listItems[0]] : [...value, listItems[value.length]])}>{complete ? <RotateCcw /> : <Plus />}{complete ? "Начать заново" : "Добавить строку"}</Button>
      <Button size="sm" variant="ghost" disabled={items.length < 2} onClick={() => setItems(value => [...value].reverse())}>Поменять порядок</Button>
    </div>
    <ul className="uk-motion-list" aria-label="Примеры разделов"><AnimatePresence initial={false}>
      {items.map(item => <motion.li key={item} layout={reduced ? false : "position"} initial={{ opacity: 0, x: reduced ? 0 : -timing.distance }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} transition={transition()}><span className="uk-list-dot" />{item}</motion.li>)}
    </AnimatePresence></ul>
    <span className="uk-caption" role="status">Разделов: {items.length}</span>
  </MotionDemo>;
}

const pages = [
  { title: "Опишите идею", text: "Расскажите, какое приложение нужно вашему бизнесу." },
  { title: "Проверьте результат", text: "Посмотрите, как клиенты будут пользоваться приложением." },
  { title: "Подготовьте запуск", text: "Настройте детали и выберите адрес приложения." },
];

function NavigationExample() {
  const { reduced, transition } = usePreviewMotion();
  const [step, setStep] = useState(0);
  const [direction, setDirection] = useState(1);
  function move(next: number) { setDirection(next > step ? 1 : -1); setStep(next); }
  return <MotionDemo title="Переход между шагами" description="Новый шаг приходит справа, предыдущий — слева. Направление помогает сохранить контекст." detail={`${milliseconds(timing.surface)} · ${timing.pageDistance} px`}>
    <div className="uk-navigation-window"><AnimatePresence initial={false} mode="wait" custom={direction}>
      <motion.div key={step} custom={direction} variants={{ enter: (value: number) => ({ opacity: 0, x: reduced ? 0 : value * timing.pageDistance }), leave: (value: number) => ({ opacity: 0, x: reduced ? 0 : -value * timing.pageDistance }) }} initial="enter" animate={{ opacity: 1, x: 0 }} exit="leave" transition={transition(timing.surface)} className="uk-step-copy">
        <strong>{pages[step].title}</strong><p>{pages[step].text}</p>
      </motion.div>
    </AnimatePresence></div>
    <div className="uk-navigation-controls"><Button size="icon" variant="secondary" aria-label="Предыдущий пример шага" disabled={step === 0} onClick={() => move(step - 1)}><ArrowLeft /></Button><span className="uk-caption" role="status">Шаг {step + 1} из {pages.length}</span><Button size="icon" variant="secondary" aria-label="Следующий пример шага" disabled={step === pages.length - 1} onClick={() => move(step + 1)}><ArrowRight /></Button></div>
  </MotionDemo>;
}

function NumberExample() {
  const { reduced, transition } = usePreviewMotion();
  const [count, setCount] = useState(12);
  return <MotionDemo title="Смена числа" description="Новое значение занимает место предыдущего без скачка строки. Уместно для небольших счётчиков." detail={`${milliseconds(timing.content)} · вертикальная смена`}>
    <div className="uk-number-line"><span>Записей на сегодня</span><span className="uk-number-window"><AnimatePresence initial={false} mode="popLayout"><motion.strong key={count} initial={{ opacity: 0, y: reduced ? 0 : 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: reduced ? 0 : -14 }} transition={transition()}>{count}</motion.strong></AnimatePresence></span></div>
    <Button variant="secondary" size="sm" onClick={() => setCount(value => value === 15 ? 12 : value + 1)}>{count === 15 ? "Сбросить счётчик" : "Добавить запись"}</Button>
    <span className="uk-caption" role="status">Демонстрационный счётчик: {count}</span>
  </MotionDemo>;
}

export function ContentMotionExamples() {
  return <><DisclosureExample /><PanelExample /><ListExample /><NavigationExample /><NumberExample /></>;
}
