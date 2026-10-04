"use client";

import { useId, useState } from "react";
import { ChevronDown, Plug } from "lucide-react";
import type { BuiltinIntegrationRequest } from "@/lib/builtin-integration-prompts";
import { cn } from "@/lib/utils";

export function IntegrationRequestCard({ request, text, expandable = true }: {
  request: BuiltinIntegrationRequest;
  text: string;
  expandable?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const headingId = useId();
  const requestId = useId();

  return (
    <section aria-labelledby={headingId} className="w-full min-w-0 max-w-2xl overflow-hidden rounded-xl border border-border-default bg-surface-raised">
      <div className="px-4 py-4">
        <div className="flex items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-surface-overlay text-fg-secondary">
            <Plug aria-hidden="true" className="size-4" />
          </div>
          <div className="min-w-0">
            <p className="text-[11px] font-medium text-fg-tertiary">Интеграция</p>
            <h3 id={headingId} className="break-words text-sm font-semibold leading-6 text-fg-primary">{request.title}</h3>
          </div>
        </div>
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm leading-6 text-fg-secondary marker:text-fg-tertiary">
          {request.outcomes.map(outcome => <li key={outcome} className="break-words">{outcome}</li>)}
        </ul>
      </div>
      {expandable && (
        <div className="border-t border-border-subtle">
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={requestId}
            onClick={() => setExpanded(current => !current)}
            className="flex min-h-11 w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-xs font-medium text-fg-secondary transition-colors hover:bg-surface-overlay hover:text-fg-primary focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent"
          >
            {expanded ? "Скрыть полный запрос" : "Раскрыть полный запрос"}
            <ChevronDown aria-hidden="true" className={cn("size-4 shrink-0 transition-transform", expanded && "rotate-180")} />
          </button>
          <p id={requestId} hidden={!expanded} className="max-w-full whitespace-pre-wrap break-words border-t border-border-subtle bg-surface-base px-4 py-3 text-sm leading-6 text-fg-secondary [overflow-wrap:anywhere]">
            {text}
          </p>
        </div>
      )}
    </section>
  );
}
