import type { ReactNode } from "react";

export function MotionDemo({ title, description, detail, children }: {
  title: string; description: string; detail: string; children: ReactNode;
}) {
  return <article className="uk-motion-demo" aria-label={title}>
    <div className="uk-motion-copy"><h4>{title}</h4><p>{description}</p><code>{detail}</code></div>
    <div className="uk-motion-stage">{children}</div>
  </article>;
}
