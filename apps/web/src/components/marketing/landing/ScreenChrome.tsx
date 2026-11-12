import { ChevronLeft, MoreHorizontal } from "lucide-react";
import type { ReactNode } from "react";

export function ScreenChrome({
  name,
  section,
  children,
  bar,
  compact = false,
  bodyKey,
  backAction,
}: {
  name: string;
  section: string;
  children: ReactNode;
  bar?: ReactNode;
  compact?: boolean;
  bodyKey?: string;
  backAction?: { label: string; onClick: () => void };
}) {
  return (
    <div className={`ys-screen${compact ? " ys-screen--compact" : ""}`}>
      <div className="ys-screen-top">
        {backAction ? (
          <button type="button" className="ys-screen-back" aria-label={backAction.label} onClick={backAction.onClick}>
            <ChevronLeft size={15} aria-hidden="true" />
          </button>
        ) : <ChevronLeft size={15} aria-hidden="true" />}
        <div>
          <strong>{name}</strong>
          <span>{section}</span>
        </div>
        <MoreHorizontal size={16} aria-hidden="true" />
      </div>
      <div className="ys-screen-body" key={bodyKey}>{children}</div>
      {bar && <div className="ys-bar">{bar}</div>}
    </div>
  );
}

export function PhoneFrame({ children, time = "9:41" }: { children: ReactNode; time?: string }) {
  return (
    <div className="ys-phone-frame">
      <div className="ys-phone-status" aria-hidden="true">
        <span className="ys-phone-time">{time}</span>
        <span className="ys-phone-island" />
        <span className="ys-phone-meters">
          <i className="ys-phone-signal" />
          <i className="ys-phone-battery" />
        </span>
      </div>
      <div className="ys-phone-viewport">{children}</div>
      <span className="ys-phone-home" aria-hidden="true" />
    </div>
  );
}

export function Promo({ kicker, title, pill, tone }: { kicker: string; title: string; pill?: string; tone: string }) {
  return (
    <div className={`ys-promo ys-promo--${tone}`}>
      <div>
        <span>{kicker}</span>
        <strong>{title}</strong>
      </div>
      {pill && <em>{pill}</em>}
    </div>
  );
}

export function Strip({ title, note, value }: { title: string; note: string; value: string }) {
  return (
    <div className="ys-strip">
      <div><strong>{title}</strong><small>{note}</small></div>
      <b>{value}</b>
    </div>
  );
}
