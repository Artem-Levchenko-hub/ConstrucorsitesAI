"use client";

import { useState, useSyncExternalStore, type CSSProperties } from "react";
import { ContentMotionExamples } from "./ContentMotionExamples";
import { ControlMotionExamples } from "./ControlMotionExamples";
import { FeedbackMotionExamples } from "./FeedbackMotionExamples";
import { MotionPreviewProvider } from "./MotionPreviewContext";
import { previewMotion as timing } from "./motion-preview";

const motionQuery = "(prefers-reduced-motion: reduce)";
const readMotionPreference = () => window.matchMedia?.(motionQuery).matches ?? true;
const serverMotionPreference = () => true;

function subscribeMotionPreference(onChange: () => void) {
  const media = window.matchMedia?.(motionQuery);
  media?.addEventListener("change", onChange);
  return () => media?.removeEventListener("change", onChange);
}

const groups = [
  { id: "motion-controls", title: "Контролы", description: "Отклик на нажатие, выбор и фокус.", Examples: ControlMotionExamples },
  { id: "motion-content", title: "Контент и переходы", description: "Появление, раскрытие и движение между состояниями.", Examples: ContentMotionExamples },
  { id: "motion-feedback", title: "Результаты действий", description: "Подтверждение, уведомление и понятный прогресс.", Examples: FeedbackMotionExamples },
];

export function MotionLab() {
  const systemReduced = useSyncExternalStore(subscribeMotionPreference, readMotionPreference, serverMotionPreference);
  const [reduce, setReduce] = useState(false);
  const [slow, setSlow] = useState(false);
  const reduced = systemReduced || reduce;
  const style = {
    "--uk-motion-control": `${timing.control * (slow ? 2 : 1)}s`,
    "--uk-motion-content": `${timing.content * (slow ? 2 : 1)}s`,
  } as CSSProperties;

  return <div className="uk-lab" data-reduced={reduced} data-slow={slow} style={style}>
    <div className="uk-lab-intro">
      <p>14 примеров: от отклика кнопки до смены шага. Движение стало заметнее — с мягким замедлением и без резких отскоков.</p>
      <div className="uk-lab-options">
        <label className="uk-check"><input type="checkbox" checked={reduce} onChange={event => setReduce(event.target.checked)} /> Уменьшить движение</label>
        <label className="uk-check"><input type="checkbox" checked={slow} disabled={reduced} onChange={event => setSlow(event.target.checked)} /> Замедлить для просмотра</label>
      </div>
      <p className="uk-caption" role="status">{systemReduced ? "В системе включено уменьшение движения — оно всегда имеет приоритет." : reduced ? "Только короткие переходы прозрачности. Положение меняется сразу." : slow ? "Скорость вдвое медленнее, чтобы рассмотреть детали." : "Каждый пример запускается только вашим действием."}</p>
      <nav className="uk-motion-nav" aria-label="Группы анимаций">{groups.map(group => <a key={group.id} href={`#${group.id}`}>{group.title}</a>)}</nav>
    </div>
    <MotionPreviewProvider reduced={reduced} slow={slow}>
      {groups.map(({ id, title, description, Examples }) => <section key={id} id={id} className="uk-motion-group" aria-labelledby={`${id}-title`}>
        <div className="uk-motion-group-heading"><h3 id={`${id}-title`}>{title}</h3><p>{description}</p></div><Examples />
      </section>)}
    </MotionPreviewProvider>
  </div>;
}
