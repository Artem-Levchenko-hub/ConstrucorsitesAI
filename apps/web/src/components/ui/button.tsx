import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-all focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-accent text-fg-on-accent hover:bg-accent-hover",
        secondary:
          "bg-surface-raised text-fg-primary border border-border-default hover:border-border-strong",
        ghost: "text-fg-primary hover:bg-surface-raised",
        danger: "bg-danger text-fg-on-accent hover:opacity-90",
        // Magnetic primary CTA — mirrors landing `.om-btn-primary-mag`:
        // violet gradient + glow shadow, fully rounded pill, slight lift on hover.
        "pill-primary":
          "rounded-full bg-[linear-gradient(135deg,#7c5cff_0%,#a48aff_100%)] text-white shadow-[0_20px_50px_-16px_rgba(124,92,255,0.7),inset_0_0_0_1px_rgba(255,255,255,0.08)] hover:shadow-[0_24px_60px_-14px_rgba(124,92,255,0.85),inset_0_0_0_1px_rgba(255,255,255,0.12)] hover:-translate-y-px",
        "pill-secondary":
          "rounded-full bg-surface-glass text-fg-primary border border-border-default backdrop-blur-md hover:border-border-strong hover:bg-surface-overlay",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        md: "h-9 px-4",
        lg: "h-11 px-6 text-base",
        xl: "h-12 px-7 text-base",
        icon: "h-9 w-9",
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
