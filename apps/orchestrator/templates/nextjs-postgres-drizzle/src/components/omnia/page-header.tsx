/**
 * `<PageHeader>` — the standard cabinet screen heading: an optional quiet
 * eyebrow, a large title, a muted lead, and a right-aligned actions slot. Renders
 * through `<ScreenFrame>` so the title sits on a quiet `--brand` plinth — the
 * calm sibling of the dashboard hero — giving each sub-page a confident, coherent
 * anchor instead of flat body text. Pure and server-component-safe (no hooks),
 * self-contained on Tailwind + the `--brand` token.
 */
import * as React from "react";

import { ScreenFrame } from "./screen-frame";

export interface PageHeaderProps {
  /** Small uppercase label above the title (e.g. «Обзор», a breadcrumb). */
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Right-aligned controls (buttons, filters). Wraps below the title on
   *  narrow screens. */
  actions?: React.ReactNode;
}

export function PageHeader({ eyebrow, title, description, actions }: PageHeaderProps) {
  return (
    <ScreenFrame
      variant="list"
      eyebrow={eyebrow}
      title={title}
      description={description}
      actions={actions}
    />
  );
}
