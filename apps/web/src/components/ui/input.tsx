import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, type, ...props }, ref) => (
  <input
    type={type}
    ref={ref}
    className={cn(
      "flex h-10 w-full rounded-lg border border-border-default bg-surface-input px-3.5 py-2 text-sm text-fg-primary shadow-sm transition-[border-color,box-shadow,background-color] placeholder:text-fg-tertiary focus-visible:border-accent focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-accent/10 disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
Input.displayName = "Input";
