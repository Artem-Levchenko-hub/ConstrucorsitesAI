"use client";

import { Bell, Heart } from "lucide-react";
import { MAX_FEATURE_INFO, type MaxFeature, type MaxFeatureScreen } from "@/lib/max-brief";
import "./max-feature-preview.css";

/** Схематичные экраны: показывают состав функции, а не будущий дизайн. */
function Screen({ kind }: { kind: MaxFeatureScreen }) {
  if (kind === "profile") return <>
    <div className="mfp-person"><span className="mfp-avatar" /><span className="mfp-stack"><span className="mfp-line mfp-line--strong" style={{ width: "62%" }} /><span className="mfp-line" style={{ width: "40%" }} /></span></div>
    {[0, 1, 2].map(row => <span key={row} className="mfp-row"><span className="mfp-line" style={{ width: `${60 - row * 8}%` }} /><span className="mfp-caret" /></span>)}
  </>;

  if (kind === "catalog") return <>
    <span className="mfp-line mfp-line--strong" style={{ width: "48%" }} />
    <div className="mfp-grid">{[0, 1, 2, 3].map(tile => <span key={tile} className="mfp-tile">
      <span className="mfp-thumb" /><span className="mfp-line" style={{ width: "76%" }} /><span className="mfp-price" />
    </span>)}</div>
  </>;

  if (kind === "search") return <>
    <span className="mfp-search"><span className="mfp-lens" /><span className="mfp-line" style={{ width: "44%" }} /></span>
    <div className="mfp-chips">{[0, 1, 2].map(chip => <span key={chip} className="mfp-chip" data-active={chip === 0} />)}</div>
    {[0, 1, 2].map(row => <span key={row} className="mfp-wide-row"><span className="mfp-thumb mfp-thumb--small" /><span className="mfp-stack"><span className="mfp-line mfp-line--strong" style={{ width: "70%" }} /><span className="mfp-line" style={{ width: "45%" }} /></span></span>)}
  </>;

  if (kind === "favorites") return <>
    <span className="mfp-line mfp-line--strong" style={{ width: "40%" }} />
    {[0, 1].map(row => <span key={row} className="mfp-wide-row"><span className="mfp-thumb mfp-thumb--small" /><span className="mfp-stack"><span className="mfp-line mfp-line--strong" style={{ width: "66%" }} /><span className="mfp-line" style={{ width: "38%" }} /></span><Heart className="mfp-icon mfp-icon--liked" /></span>)}
  </>;

  if (kind === "loyalty") return <>
    <span className="mfp-hero"><span className="mfp-hero-value">480</span><span className="mfp-hero-caption" />
      <span className="mfp-progress"><span /></span>
    </span>
    {[0, 1].map(row => <span key={row} className="mfp-row"><span className="mfp-stack"><span className="mfp-line mfp-line--strong" style={{ width: "58%" }} /><span className="mfp-line" style={{ width: "32%" }} /></span><span className="mfp-pill" /></span>)}
  </>;

  if (kind === "booking") return <>
    <div className="mfp-days">{[0, 1, 2, 3, 4].map(day => <span key={day} className="mfp-day" data-active={day === 2} />)}</div>
    <div className="mfp-slots">{[0, 1, 2, 3, 4, 5].map(slot => <span key={slot} className="mfp-slot" data-active={slot === 3} />)}</div>
    <span className="mfp-button" />
  </>;

  if (kind === "notifications") return <>
    <span className="mfp-bubble"><span className="mfp-bot"><Bell className="mfp-icon" /></span><span className="mfp-stack"><span className="mfp-line mfp-line--strong" style={{ width: "72%" }} /><span className="mfp-line" style={{ width: "54%" }} /><span className="mfp-line" style={{ width: "34%" }} /></span></span>
    <span className="mfp-button mfp-button--ghost" />
  </>;

  return <div className="mfp-timeline">{[0, 1, 2].map(entry => <span key={entry} className="mfp-entry">
    <span className="mfp-dot" />
    <span className="mfp-stack"><span className="mfp-line mfp-line--strong" style={{ width: `${68 - entry * 10}%` }} /><span className="mfp-line" style={{ width: "36%" }} /></span>
  </span>)}</div>;
}

/** Правый столбец мастера: пример экрана выбранной функции. */
export function MaxFeaturePreview({ feature }: { feature: MaxFeature | null }) {
  const info = feature ? MAX_FEATURE_INFO[feature] : null;

  return (
    <aside className="max-feature-preview" data-empty={!feature} aria-live="polite">
      <div className="max-feature-preview__screen" aria-hidden="true">
        <span className="max-feature-preview__notch" />
        <div className="max-feature-preview__canvas">
          {info ? <Screen kind={info.screen} /> : <span className="max-feature-preview__blank" />}
        </div>
      </div>
      <div className="max-feature-preview__copy">
        {feature && info ? <>
          <p className="max-feature-preview__eyebrow">Пример экрана</p>
          <h4>{feature}</h4>
          <p>{info.summary}</p>
          <p className="max-feature-preview__note">Это схема состава, а не готовый дизайн: точный вид ИИ соберёт под ваш продукт.</p>
        </> : <>
          <p className="max-feature-preview__eyebrow">Пример экрана</p>
          <h4>Наведите на значок глаза</h4>
          <p>У каждой функции есть кнопка предпросмотра — покажем, что появится в приложении.</p>
        </>}
      </div>
    </aside>
  );
}
