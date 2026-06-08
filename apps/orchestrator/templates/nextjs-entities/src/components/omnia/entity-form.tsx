"use client";

import * as React from "react";
import { ImagePlus, Loader2, X } from "lucide-react";
import { toast } from "sonner";

import { entities, integrations, type Row } from "@/lib/sdk";
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

  function buildPayload(): Record<string, unknown> | null {
    const out: Record<string, unknown> = {};
    for (const f of fields) {
      const raw = values[f.name];
      if (f.kind === "boolean") {
        out[f.name] = Boolean(raw);
        continue;
      }
      const empty = raw === "" || raw == null;
      if (empty) {
        if (f.required) {
          toast.error(`Заполните поле «${f.label}»`);
          return null;
        }
        continue;
      }
      out[f.name] = f.kind === "number" ? Number(raw) : raw;
    }
    return out;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const payload = buildPayload();
    if (!payload) return;
    setSubmitting(true);
    try {
      await onSubmit(payload);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {fields.map((f) => {
        const id = `field-${f.name}`;
        const value = values[f.name];
        return (
          <div key={f.name} className="space-y-2">
            {f.kind !== "boolean" && (
              <Label htmlFor={id}>
                {f.label}
                {f.required ? <span className="text-destructive">*</span> : null}
              </Label>
            )}

            {f.kind === "text" && (
              <Input
                id={id}
                value={String(value ?? "")}
                placeholder={f.placeholder}
                onChange={(e) => set(f.name, e.target.value)}
              />
            )}

            {f.kind === "textarea" && (
              <Textarea
                id={id}
                value={String(value ?? "")}
                placeholder={f.placeholder}
                onChange={(e) => set(f.name, e.target.value)}
              />
            )}

            {f.kind === "number" && (
              <Input
                id={id}
                type="number"
                value={value === "" || value == null ? "" : String(value)}
                placeholder={f.placeholder}
                onChange={(e) => set(f.name, e.target.value)}
              />
            )}

            {f.kind === "date" && (
              <Input
                id={id}
                type="date"
                value={toDateInput(value)}
                onChange={(e) => set(f.name, e.target.value)}
              />
            )}

            {f.kind === "boolean" && (
              <Label htmlFor={id} className="cursor-pointer">
                <Checkbox
                  id={id}
                  checked={Boolean(value)}
                  onCheckedChange={(c) => set(f.name, c === true)}
                />
                {f.label}
              </Label>
            )}

            {(f.kind === "select" || f.kind === "reference") && (
              <Select
                value={value ? String(value) : undefined}
                onValueChange={(v) => set(f.name, v)}
              >
                <SelectTrigger id={id}>
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
          </div>
        );
      })}

      <div className="flex justify-end gap-2 pt-2">
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
