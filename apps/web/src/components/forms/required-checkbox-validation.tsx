"use client";

import { useId, useState, type ChangeEventHandler, type InputHTMLAttributes } from "react";

const message = "Поставьте галочку, чтобы продолжить.";
type ValidationError = { id: string; message: string } | null;
type ValidationProps = Pick<InputHTMLAttributes<HTMLInputElement>, "onInvalid" | "onChange" | "aria-invalid" | "aria-describedby">;

/** One hook per form; propsFor can be used inside its existing checkbox map. */
export function useRequiredCheckboxValidation(scope?: string) {
  const prefix = useId();
  const [invalid, setInvalid] = useState<{ scope?: string; fields: Record<string, boolean> }>({ scope, fields: {} });
  const fields = invalid.scope === scope ? invalid.fields : {};

  function errorFor(key: string): ValidationError {
    return fields[key] ? { id: `${prefix}-${key}-error`, message } : null;
  }

  function propsFor(key: string, onChange?: ChangeEventHandler<HTMLInputElement>): ValidationProps {
    const error = errorFor(key);
    return {
      "aria-invalid": error ? true : undefined,
      "aria-describedby": error?.id,
      onInvalid: event => {
        const input = event.currentTarget;
        if (!input.required || input.checked || input.disabled) return;
        input.setCustomValidity(message);
        setInvalid(previous => ({ scope, fields: { ...(previous.scope === scope ? previous.fields : {}), [key]: true } }));
      },
      onChange: event => {
        event.currentTarget.setCustomValidity("");
        setInvalid(previous => previous.scope === scope && previous.fields[key]
          ? { scope, fields: { ...previous.fields, [key]: false } } : previous);
        onChange?.(event);
      },
    };
  }

  return { propsFor, errorFor };
}

export function RequiredCheckboxMessage({ error }: { error: ValidationError }) {
  return error ? <small id={error.id} role="alert" className="mt-1 block text-xs leading-5 text-danger-fg">{error.message}</small> : null;
}
