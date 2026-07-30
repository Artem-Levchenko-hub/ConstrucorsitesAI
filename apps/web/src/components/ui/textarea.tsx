import * as React from "react";
import { cn } from "@/lib/utils";

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "flex min-h-[80px] w-full rounded-lg border border-border-default bg-surface-input px-3.5 py-3 text-sm text-fg-primary shadow-sm transition-[border-color,box-shadow] placeholder:text-fg-tertiary focus-visible:border-accent focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-accent/10 disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";
