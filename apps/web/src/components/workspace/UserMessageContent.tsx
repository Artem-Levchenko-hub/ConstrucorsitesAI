"use client";

import { useId, useState } from "react";
import { cn } from "@/lib/utils";
import { userMessageLayers, type UserMessageGroup } from "@/lib/user-message-presentation";

const textClass = "min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere] text-sm leading-6 text-inherit";
const summaryClass = "min-h-11 cursor-pointer py-2 text-xs font-medium text-inherit hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

/** Original text stays untouched; this component only changes how it is read. */
export function UserMessageContent({ text, plain = false }: {
  text: string; plain?: boolean;
}) {
  const layers = userMessageLayers(text);
  const presentation = layers[0];
  if (!presentation) return <UserText text={text} plain={plain} />;
  const title = layers.find(layer => layer.title)?.title;
  const task = layers.at(-1)!.task;
  return <div className={cn("min-w-0 max-w-full space-y-2", !plain && "rounded-2xl rounded-tl-md border border-accent/20 bg-accent-subtle/50 px-3.5 py-2")}
    data-user-message-presentation={presentation.kind}>
    {title && <h3 className="[overflow-wrap:anywhere] text-base font-semibold leading-6">{title}</h3>}
    {task && <UserText text={task} plain />}
    <div className="divide-y divide-border-subtle">
      {presentation.groups.map(group => <details key={group.title}>
        <summary className={summaryClass}>{group.title}</summary>
        <Fields group={group} />
      </details>)}
      {layers.length > 1 && <details>
        <summary className={summaryClass}>Параметры из вложенных описаний</summary>
        {layers.slice(1).map((layer, index) => <section key={index} className="space-y-3 pb-3">
          {layer.title && <h4 className="[overflow-wrap:anywhere] text-sm font-semibold">{layer.title}</h4>}
          {layer.groups.map(group => <div key={group.title}>
            <h5 className="mb-2 text-sm font-medium">{group.title}</h5>
            <Fields group={group} />
          </div>)}
        </section>)}
      </details>}
      <details>
        <summary className={summaryClass}>Полный исходный запрос</summary>
        <div className={cn(textClass, "pb-3")} data-user-message-original>{text}</div>
      </details>
    </div>
  </div>;
}

function Fields({ group }: { group: UserMessageGroup }) {
  return <dl className="space-y-3 pb-3">
    {group.fields.map((item, index) => <div key={`${index}:${item.label}`} className="min-w-0">
      <dt className="text-xs font-medium leading-5">{item.label}</dt>
      <dd className={textClass}>{item.value}</dd>
    </div>)}
  </dl>;
}

function UserText({ text, plain }: { text: string; plain: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const contentId = useId();
  const collapsible = plain && text.length > 700;
  return <div>
    <div id={contentId} className={cn(textClass,
      !plain && "inline-block max-w-full rounded-2xl rounded-tl-md border border-accent/20 bg-accent-subtle/50 px-3.5 py-2",
      collapsible && !expanded && "max-h-[240px] overflow-hidden",
    )}>{text}</div>
    {collapsible && <button type="button" aria-expanded={expanded} aria-controls={contentId}
      onClick={() => setExpanded(current => !current)}
      className="mt-2 min-h-11 text-xs font-medium text-inherit underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
      {expanded ? "Свернуть" : "Показать полностью"}
    </button>}
  </div>;
}
