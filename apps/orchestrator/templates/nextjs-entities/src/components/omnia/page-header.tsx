import * as React from "react";

import { ScreenFrame } from "./screen-frame";

export interface PageHeaderProps {
  /** Small uppercase label above the title (section / context), e.g. “Обзор”.
   *  Gives the title hierarchy weight without extra size — type doing graphic
   *  work, restrained. Optional. */
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Right-aligned controls (e.g. a “Создать” button). */
  actions?: React.ReactNode;
  className?: string;
}

/** Consistent page heading for every non-dashboard screen: optional eyebrow +
 *  display title + description on the left, actions on the right; stacks on
 *  mobile. Renders through `<ScreenFrame>` so the title sits on a quiet brand
 *  plinth — the calm sibling of the dashboard hero — giving each list/CRUD screen
 *  a confident, coherent anchor instead of flat body text. Put one at the top of
 *  every app screen (use `<DashboardHero>` on the dashboard itself). */
export function PageHeader({ eyebrow, title, description, actions, className }: PageHeaderProps) {
  return (
    <ScreenFrame
      variant="list"
      eyebrow={eyebrow}
      title={title}
      description={description}
      actions={actions}
      className={className}
    />
  );
}
