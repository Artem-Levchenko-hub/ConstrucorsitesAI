"use client";

import * as React from "react";
import { ImagePlus, Loader2, X } from "lucide-react";
import { toast } from "sonner";

import { entities, integrations, type Row } from "@/lib/sdk";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export type FieldKind =
  | "text"
  | "textarea"
  | "number"
  | "boolean"
  | "date"
  | "select"
  | "reference"
  | "image";

export interface FieldSpec {
  name: string;
  label: string;
  kind: FieldKind;
  required?: boolean;
  placeholder?: string;
  /** Optional helper text shown under the label (e.g. format hints, context). */
  hint?: string;
  /** For `kind: "select"`. */
  options?: { value: string; label: string }[];
  /** For `kind: "reference"` — entity to load options from. */
  refEntity?: string;
  /** Field on the referenced row to show (defaults to name/title/label). */
  refLabel?: string;
  default?: unknown;
}

export interface EntityFormProps {
  fields: FieldSpec[];
  /** Existing row for edit mode; omit for create. */
  initial?: Record<string, unknown>;
  submitLabel?: string;
  onSubmit: (data: Record<string, unknown>) => Promise<void> | void;
  onCancel?: () => void;
}

function refRowLabel(row: Row, refLabel?: string): string {
  const v =
    (refLabel && row[refLabel]) ?? row.name ?? row.title ?? row.label ?? row.id;
  return String(v);
}

function toDateInput(value: unknown): string {
  if (!value) return "";
  const d = new Date(value as string);
  return Number.isNaN(d.getTime()) ? "" : d.toISOString().slice(0, 10);
}

/**
 * Schema-driven form for one entity row. Renders the right control per field
 * kind, loads options for `reference` fields, and uploads `image` fields to
 * storage. Used by <CrudResource>; usable standalone for custom create/edit.
 */
export function EntityForm({
  fields,
  initial,
  submitLabel = "Сохранить",
  onSubmit,
  onCancel,
}: EntityFormProps) {
  const [values, setValues] = React.useState<Record<string, unknown>>(() => {
    const seed: Record<string, unknown> = {};
    for (const f of fields) {
      seed[f.name] = initial?.[f.name] ?? f.default ?? (f.kind === "boolean" ? false : "");
    }
    return seed;
  });
  const [refOptions, setRefOptions] = React.useState<
    Record<string, { value: string; label: string }[]>
  >({});
  const [uploading, setUploading] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [errors, setErrors] = React.useState<Record<string, string>>({});

  React.useEffect(() => {
    let alive = true;
    const refs = fields.filter((f) => f.kind === "reference" && f.refEntity);
    if (!refs.length) return;
    (async () => {
      const entries = await Promise.all(
        refs.map(async (f) => {
          try {
            const rows = await entities[f.refEntity as string].list({ limit: 200 });
            return [
              f.name,
              rows.map((r) => ({ value: r.id, label: refRowLabel(r, f.refLabel) })),
            ] as const;
          } catch {
            return [f.name, []] as const;
          }
        }),
      );
      if (alive) setRefOptions(Object.fromEntries(entries));
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function set(name: string, value: unknown) {
    setValues((v) => ({ ...v, [name]: value }));
    setErrors((e) => {
      if (!e[name]) return e;
      const next = { ...e };
      delete next[name];
      return next;
    });
  }

  async function handleFile(name: string, file: File | undefined) {
    if (!file) return;
    setUploading(name);
    try {
      const res = await integrations.uploadFile(file);
      set(name, res.url);
    } catch {
      toast.error("Не удалось загрузить файл");
    } finally {
      setUploading(null);
    }
  }

  type ValidationResult =
    | { ok: true; payload: Record<string, unknown> }
    | { ok: false; errors: Record<string, string> };

  function validate(): ValidationResult {
    const out: Record<string, unknown> = {};
    const errs: Record<string, string> = {};
    for (const f of fields) {
      const raw = values[f.name];
      if (f.kind === "boolean") {
        out[f.name] = Boolean(raw);
        continue;
      }
      const empty = raw === "" || raw == null;
      if (empty) {
        if (f.required) errs[f.name] = `Заполните поле «${f.label}»`;
        continue;
      }
      out[f.name] = f.kind === "number" ? Number(raw) : raw;
    }
    if (Object.keys(errs).length) return { ok: false, errors: errs };
    return { ok: true, payload: out };
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const result = validate();
    if (!result.ok) {
      // Inline errors + focus first invalid (WCAG focus-management): no blind toast.
      setErrors(result.errors);
      const firstInvalid = fields.find((f) => result.errors[f.name]);
      if (firstInvalid) document.getElementById(`field-${firstInvalid.name}`)?.focus();
      return;
    }
    setErrors({});
    setSubmitting(true);
    try {
      await onSubmit(result.payload);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="grid grid-cols-1 gap-x-5 gap-y-5 sm:grid-cols-2">
        {fields.map((f) => {
          const id = `field-${f.name}`;
          const value = values[f.name];
          const error = errors[f.name];
          const errId = error ? `${id}-error` : undefined;
          // Wide controls (long text, media), the lone field, and the boxed
          // boolean toggle each claim the full row so the grid reads balanced.
          const wide =
            f.kind === "textarea" ||
            f.kind === "image" ||
            f.kind === "boolean" ||
            fields.length === 1;
          return (
            <div
              key={f.name}
              className={cn("space-y-2", wide && "sm:col-span-2")}
            >
              {f.kind !== "boolean" && (
                <div className="space-y-1">
                  <Label htmlFor={id}>
                    {f.label}
                    {f.required ? (
                      <span className="ml-0.5 text-destructive">*</span>
                    ) : null}
                  </Label>
                  {f.hint ? (
                    <p className="text-xs text-muted-foreground">{f.hint}</p>
                  ) : null}
                </div>
              )}

              {f.kind === "text" && (
              <Input
                id={id}
                value={String(value ?? "")}
                placeholder={f.placeholder}
                aria-invalid={error ? true : undefined}
                aria-describedby={errId}
                onChange={(e) => set(f.name, e.target.value)}
              />
            )}

            {f.kind === "textarea" && (
              <Textarea
                id={id}
                value={String(value ?? "")}
                placeholder={f.placeholder}
                aria-invalid={error ? true : undefined}
                aria-describedby={errId}
                onChange={(e) => set(f.name, e.target.value)}
              />
            )}

            {f.kind === "number" && (
              <Input
                id={id}
                type="number"
                value={value === "" || value == null ? "" : String(value)}
                placeholder={f.placeholder}
                aria-invalid={error ? true : undefined}
                aria-describedby={errId}
                onChange={(e) => set(f.name, e.target.value)}
              />
            )}

            {f.kind === "date" && (
              <Input
                id={id}
                type="date"
                value={toDateInput(value)}
                aria-invalid={error ? true : undefined}
                aria-describedby={errId}
                onChange={(e) => set(f.name, e.target.value)}
              />
            )}

            {f.kind === "boolean" && (
              <Label
                htmlFor={id}
                className="flex cursor-pointer items-start justify-between gap-4 rounded-lg border border-border bg-muted/30 p-4 transition-colors hover:bg-muted/50"
              >
                <span className="space-y-1">
                  <span className="block font-medium leading-none">{f.label}</span>
                  {f.hint ? (
                    <span className="block text-xs font-normal text-muted-foreground">
                      {f.hint}
                    </span>
                  ) : null}
                </span>
                <Checkbox
                  id={id}
                  checked={Boolean(value)}
                  onCheckedChange={(c) => set(f.name, c === true)}
                  className="mt-0.5"
                />
              </Label>
            )}

            {(f.kind === "select" || f.kind === "reference") && (
              <Select
                value={value ? String(value) : undefined}
                onValueChange={(v) => set(f.name, v)}
              >
                <SelectTrigger
                  id={id}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={errId}
                >
                  <SelectValue placeholder={f.placeholder ?? "Выберите…"} />
                </SelectTrigger>
                <SelectContent>
                  {(f.kind === "reference" ? refOptions[f.name] ?? [] : f.options ?? []).map(
                    (o) => (
                      <SelectItem key={o.value} value={o.value}>
                        {o.label}
                      </SelectItem>
                    ),
                  )}
                </SelectContent>
              </Select>
            )}

            {f.kind === "image" && (
              <div className="flex items-center gap-3">
                {value ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={String(value)}
                    alt=""
                    className="size-16 rounded-lg border border-border object-cover"
                  />
                ) : (
                  <div className="flex size-16 items-center justify-center rounded-lg border border-dashed border-border text-muted-foreground">
                    <ImagePlus className="size-5" />
                  </div>
                )}
                <div className="flex items-center gap-2">
                  <Button asChild variant="outline" size="sm" disabled={uploading === f.name}>
                    <label className="cursor-pointer">
                      {uploading === f.name ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : null}
                      {value ? "Заменить" : "Загрузить"}
                      <input
                        type="file"
                        accept="image/*"
                        className="hidden"
                        onChange={(e) => handleFile(f.name, e.target.files?.[0])}
                      />
                    </label>
                  </Button>
                  {value ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => set(f.name, "")}
                    >
                      <X className="size-4" />
                    </Button>
                  ) : null}
                </div>
              </div>
            )}

            {error ? (
              <p id={errId} role="alert" className="text-sm font-medium text-destructive">
                {error}
              </p>
            ) : null}
            </div>
          );
        })}
      </div>

      <div className="flex justify-end gap-2 border-t border-border pt-4">
        {onCancel ? (
          <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
            Отмена
          </Button>
        ) : null}
        <Button type="submit" disabled={submitting || uploading !== null}>
          {submitting ? <Loader2 className="size-4 animate-spin" /> : null}
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
