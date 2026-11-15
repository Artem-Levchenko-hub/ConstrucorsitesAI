"use client";

import { Eye, EyeOff } from "lucide-react";
import { useState, type InputHTMLAttributes } from "react";
import { Input } from "@/components/ui/input";

type PasswordFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "id"> & {
  id: string;
  showLabel: string;
  hideLabel: string;
};

export function PasswordField({ showLabel, hideLabel, className, ...props }: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);
  const Icon = visible ? Eye : EyeOff;

  return (
    <div className="relative">
      <Input
        {...props}
        type={visible ? "text" : "password"}
        className={`${className ?? ""} pr-14 [&::-ms-reveal]:hidden`}
      />
      <button
        type="button"
        aria-label={visible ? hideLabel : showLabel}
        aria-controls={props.id}
        disabled={props.disabled}
        onPointerDown={event => { if (event.button === 0) event.preventDefault(); }}
        onClick={() => setVisible(previous => !previous)}
        className="absolute right-1 top-1/2 inline-flex size-11 max-h-full -translate-y-1/2 items-center justify-center rounded-md text-fg-secondary hover:text-fg-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <Icon size={19} aria-hidden="true" />
      </button>
    </div>
  );
}
