"use client";

import { useId, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MotionDemo } from "./MotionDemo";
import { milliseconds, usePreviewMotion } from "./MotionPreviewContext";
import { previewMotion as timing } from "./motion-preview";

function ConfirmationExample() {
  const { reduced, transition } = usePreviewMotion();
  const [saved, setSaved] = useState(false);
  return <MotionDemo title="Подтверждение" description="Текст сменяется знаком успеха на месте действия. Результат заметен без отдельного баннера." detail={`${milliseconds(timing.content)} · смена текста и масштаба`}>
    <Button variant="secondary" className="uk-confirm" onClick={() => setSaved(value => !value)}>
      <AnimatePresence initial={false} mode="wait"><motion.span key={String(saved)} initial={{ opacity: 0, y: reduced ? 0 : 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: reduced ? 0 : -6 }} transition={transition(timing.control)}>{saved ? <><Check size={16} /> Сохранено</> : "Сохранить пример"}</motion.span></AnimatePresence>
    </Button><span className="uk-caption" role="status">{saved ? "Готово. Нажмите, чтобы повторить." : "Изменения останутся только в этом примере."}</span>
  </MotionDemo>;
}

function NotificationExample() {
  const { transition, arrive, leave } = usePreviewMotion();
  const [visible, setVisible] = useState(false);
  return <MotionDemo title="Уведомление" description="Короткое сообщение появляется снизу и остаётся до закрытия. Не исчезает, пока его читают." detail={`${milliseconds(timing.surface)} · ${timing.distance} px`}>
    <Button variant="secondary" onClick={() => setVisible(value => !value)}>{visible ? "Скрыть уведомление" : "Показать уведомление"}</Button>
    <div className="uk-toast-slot"><AnimatePresence initial={false}>{visible && <motion.div className="uk-demo-toast" initial={arrive} animate={{ opacity: 1, y: 0, scale: 1 }} exit={leave} transition={transition(timing.surface)}>
      <Check size={18} aria-hidden="true" /><div role="status"><strong>Черновик сохранён</strong><p>Можно продолжать работу.</p></div><button type="button" aria-label="Закрыть пример уведомления" onClick={() => setVisible(false)}><X size={16} /></button>
    </motion.div>}</AnimatePresence></div>
  </MotionDemo>;
}

const progressLabels = ["Идея описана", "Приложение собрано", "Детали проверены", "Всё готово"];

function ProgressExample() {
  const { reduced, transition, instant } = usePreviewMotion();
  const [step, setStep] = useState(0);
  const percent = (step + 1) * 25;
  const id = useId();
  return <MotionDemo title="Прогресс" description="Заполненная часть спокойно догоняет фактическое состояние. Без имитации фоновой загрузки." detail={`${milliseconds(timing.progress)} · scaleX`}>
    <div className="uk-progress-example"><div className="uk-progress-label"><strong id={id}>{progressLabels[step]}</strong><span>{percent}%</span></div>
      <div className="uk-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-labelledby={id}><motion.span initial={false} animate={{ scaleX: percent / 100 }} transition={reduced ? instant : transition(timing.progress)} /></div>
    </div><Button variant="secondary" size="sm" onClick={() => setStep(value => (value + 1) % progressLabels.length)}>{step === 3 ? "Начать прогресс заново" : "Следующий этап"}</Button><span className="uk-caption" role="status">Демонстрация: {progressLabels[step].toLowerCase()}</span>
  </MotionDemo>;
}

export function FeedbackMotionExamples() {
  return <><ConfirmationExample /><NotificationExample /><ProgressExample /></>;
}
