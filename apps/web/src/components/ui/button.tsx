import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  // `active:scale-[0.97]` + the transform-aware `transition` give every button a
  // subtle Apple-style depress on press; `duration-150 ease-out` keeps it crisp.
  // Variants that need a different press (pills) override the scale after this.
  "inline-flex min-w-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-semibold transition-all duration-150 ease-out active:translate-y-px focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-45 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "border border-accent bg-accent text-fg-on-accent shadow-sm hover:bg-accent-hover hover:border-accent-hover",
        secondary:
          "bg-surface-raised text-fg-primary border border-border-default hover:border-border-strong",
        ghost: "text-fg-secondary hover:bg-surface-raised hover:text-fg-primary",
        danger: "bg-danger text-fg-on-accent hover:opacity-90",
        destructive: "bg-danger text-fg-on-accent hover:opacity-90",
        outline:
          "border border-border-default bg-transparent text-fg-primary hover:border-border-strong hover:bg-surface-raised",
        "pill-primary":
          "rounded-full bg-accent text-accent-fg hover:bg-accent-hover active:scale-[0.98] transition-transform",
        "pill-secondary":
          "rounded-full bg-surface-raised text-fg-primary border border-border-default hover:border-border-strong hover:bg-surface-overlay",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        md: "h-10 px-4",
        lg: "h-11 px-5 text-sm",
        xl: "h-12 px-6 text-base",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  };

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { buttonVariants };
