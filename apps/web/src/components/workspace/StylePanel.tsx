"use client";

import { useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Globe,
  ImageUp,
  Loader2,
  Pipette,
  RotateCcw,
  Trash2,
  Type,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  applyImagePatch,
  applyStylePatch,
  applyTextPatch,
  listFonts,
  uploadImage,
} from "@/lib/api/style";
import type { FontOption } from "@/lib/api/types";
import { useStyleEditStore } from "@/store/styleEdit";
import { useWorkspaceStore } from "@/store/workspace";
import { cn } from "@/lib/utils";

type ColorTarget = "color" | "background-color" | "border-color";
const TARGETS: { key: ColorTarget; label: string }[] = [
  { key: "color", label: "Текст" },
  { key: "background-color", label: "Фон" },
  { key: "border-color", label: "Граница" },
];
const SITE_TOKENS = ["--accent", "--primary", "--bg", "--fg", "--border"];

/** "rgb(14, 165, 233)" / "#abc" / "#aabbcc" → "#aabbcc" (fallback #000000). */
function toHex(input: string): string {
  const v = (input || "").trim();
  if (/^#[0-9a-fA-F]{6}$/.test(v)) return v.toLowerCase();
  if (/^#[0-9a-fA-F]{3}$/.test(v)) {
    return "#" + v.slice(1).split("").map((c) => c + c).join("").toLowerCase();
  }
  const m = v.match(/rgba?\(([^)]+)\)/i);
  if (m) {
    const [r, g, b] = m[1].split(",").map((n) => parseInt(n, 10));
    if ([r, g, b].every((n) => Number.isFinite(n))) {
      return (
        "#" + [r, g, b].map((n) => Math.max(0, Math.min(255, n)).toString(16).padStart(2, "0")).join("")
      );
    }
  }
  return "#000000";
}

/** `"Inter", system-ui, sans-serif` → "Inter". */
function familyName(fontFamily: string): string {
  return (fontFamily || "").split(",")[0].replace(/["']/g, "").trim() || "—";
}

const payloadKey = (t: ColorTarget) =>
  t.replace("-", "_") as "color" | "background_color" | "border_color";

export function StylePanel({
  projectId,
  post,
}: {
  projectId: string;
  post: (msg: Record<string, unknown>) => void;
}) {
  const selected = useStyleEditStore((s) => s.selected);
  const elements = useStyleEditStore((s) => s.elements);
  const tokens = useStyleEditStore((s) => s.tokens);
  const dirty = useStyleEditStore((s) => s.dirty);
  const setElementProp = useStyleEditStore((s) => s.setElementProp);
  const setToken = useStyleEditStore((s) => s.setToken);
  const clearAll = useStyleEditStore((s) => s.clearAll);
  const markSaved = useStyleEditStore((s) => s.markSaved);
  const selectSnapshot = useWorkspaceStore((s) => s.selectSnapshot);
  const qc = useQueryClient();

  const [target, setTarget] = useState<ColorTarget>("color");
  const [siteWide, setSiteWide] = useState(false);
  const [token, setTokenSel] = useState<string>("--accent");
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [chosenSrc, setChosenSrc] = useState<string | null>(null);
  const [savingText, setSavingText] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);

  const { data: fonts } = useQuery({
    queryKey: ["fonts"],
    queryFn: listFonts,
    staleTime: Infinity,
  });

  const currentHex = useMemo(() => {
    if (!selected) return "#000000";
    const edited = elements[selected.selector]?.[payloadKey(target)];
    const computed =
      target === "color"
        ? selected.color
        : target === "background-color"
          ? selected.backgroundColor
          : selected.borderColor;
    return toHex(edited ?? computed);
  }, [selected, elements, target]);

  if (!selected) return null;

  // Images at the click point — a carousel stacks several; let the user choose
  // which one to replace (defaults to the topmost).
  const imgSrcs =
    selected.srcs && selected.srcs.length
      ? selected.srcs
      : selected.src
        ? [selected.src]
        : [];
  const activeSrc =
    chosenSrc && imgSrcs.includes(chosenSrc) ? chosenSrc : (imgSrcs[0] ?? "");

  const applyColor = (hex: string) => {
    if (siteWide) {
      setToken(token, hex);
      post({ type: "omnia:style:set", target: "token", prop: token, value: hex });
    } else {
      setElementProp(selected.selector, payloadKey(target), hex);
      post({
        type: "omnia:style:set",
        target: "element",
        selector: selected.selector,
        prop: target,
        value: hex,
      });
    }
  };

  const pickEyedropper = async () => {
    const ED = (window as unknown as { EyeDropper?: new () => { open: () => Promise<{ sRGBHex: string }> } }).EyeDropper;
    if (!ED) return;
    try {
      const { sRGBHex } = await new ED().open();
      if (sRGBHex) applyColor(sRGBHex);
    } catch {
      /* user cancelled the eyedropper */
    }
  };

  const applyFont = (f: FontOption) => {
    post({ type: "omnia:font:link", family: f.family, href: f.google_fonts_url });
    post({
      type: "omnia:style:set",
      target: "element",
      selector: selected.selector,
      prop: "font-family",
      value: f.css_stack,
    });
    setElementProp(selected.selector, "font_family", f.family);
  };

  const reset = () => {
    post({ type: "omnia:style:reset" });
    clearAll();
  };

  const save = async () => {
    const payload = {
      tokens: Object.entries(tokens).map(([v, value]) => ({ var: v, value })),
      elements: Object.entries(elements).map(([selector, e]) => ({ selector, ...e })),
    };
    setSaving(true);
    try {
      await applyStylePatch(projectId, payload);
      await qc.invalidateQueries({ queryKey: ["snapshots", projectId] });
      selectSnapshot(null);
      markSaved();
      toast.success("Стиль сохранён — новая версия в истории");
    } catch (e) {
      toast.error("Не удалось сохранить стиль", {
        description: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setSaving(false);
    }
  };

  // Replace this image with the user's own upload — commits a snapshot, no LLM.
  const replaceImage = async (file: File) => {
    if (!activeSrc || uploading) return;
    if (!file.type.startsWith("image/")) {
      toast.error("Это не изображение");
      return;
    }
    setUploading(true);
    try {
      const { url } = await uploadImage(projectId, file);
      await applyImagePatch(projectId, { old_src: activeSrc, new_src: url });
      await qc.invalidateQueries({ queryKey: ["snapshots", projectId] });
      selectSnapshot(null);
      clearAll();
      toast.success("Фото заменено — новая версия в истории");
    } catch (e) {
      toast.error("Не удалось заменить фото", {
        description: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setUploading(false);
    }
  };

  // Save direct text edit — commits a snapshot, no LLM.
  const saveText = async () => {
    if (!selected?.editableText || savingText) return;
    const next = textRef.current?.value ?? "";
    const prev = selected.editText ?? "";
    if (next === prev) return;
    setSavingText(true);
    try {
      await applyTextPatch(projectId, {
        old_text: prev,
        new_text: next,
        index: selected.textIndex ?? 0,
      });
      await qc.invalidateQueries({ queryKey: ["snapshots", projectId] });
      selectSnapshot(null);
      clearAll();
      toast.success("Текст изменён — новая версия в истории");
    } catch (e) {
      toast.error("Не удалось изменить текст", {
        description: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setSavingText(false);
    }
  };

  // Remove the element from the page — hides it (display:none) via the overrides
  // block. Reversible (a snapshot in history); works for any element.
  const deleteElement = async () => {
    if (!selected || deleting) return;
    setDeleting(true);
    try {
      await applyStylePatch(projectId, {
        tokens: [],
        elements: [{ selector: selected.selector, hidden: true }],
      });
      await qc.invalidateQueries({ queryKey: ["snapshots", projectId] });
      selectSnapshot(null);
      clearAll();
      toast.success("Элемент убран — новая версия в истории");
    } catch (e) {
      toast.error("Не удалось убрать элемент", {
        description: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setDeleting(false);
    }
  };

  const activeFamily = familyName(
    elements[selected.selector]?.font_family ?? selected.fontFamily,
  );
  const hasEyeDropper = typeof window !== "undefined" && "EyeDropper" in window;

  return (
    <div className="absolute right-3 bottom-3 z-40 w-72 rounded-xl border border-border-default bg-surface-panel-dark shadow-2xl flex flex-col max-h-[calc(100%-1.5rem)]">
      <div className="flex items-center gap-2 px-3 h-10 border-b border-border-subtle shrink-0">
        <Pipette className="h-4 w-4 text-accent" />
        <span className="text-xs font-medium text-fg-primary">Стиль элемента</span>
        <span className="text-[11px] font-mono text-fg-tertiary truncate">
          {selected.tag}
        </span>
        <button
          type="button"
          onClick={clearAll}
          className="ml-auto text-fg-tertiary hover:text-fg-primary transition-colors"
          title="Закрыть"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="p-3 space-y-4 overflow-y-auto scrollbar-elegant">
        {/* TEXT — edit the element's content directly (no LLM) */}
        {selected.editableText ? (
          <div className="space-y-2">
            <div className="flex items-center gap-1.5 text-[11px] text-fg-tertiary">
              <Type className="h-3 w-3" />
              Текст — редактируй прямо тут
            </div>
            <textarea
              key={selected.selector}
              ref={textRef}
              defaultValue={selected.editText ?? ""}
              rows={3}
              className="w-full rounded-lg border border-border-default bg-surface-input px-2.5 py-2 text-sm text-fg-primary focus:outline-none focus:ring-1 focus:ring-accent resize-y"
            />
            <Button
              size="sm"
              onClick={saveText}
              disabled={savingText}
              className="w-full gap-1.5"
            >
              {savingText ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Check className="h-3.5 w-3.5" />
              )}
              Сохранить текст
            </Button>
          </div>
        ) : null}

        {/* IMAGE — replace a generated picture with your own (no LLM) */}
        {imgSrcs.length > 0 ? (
          <div className="space-y-2">
            <div className="flex items-center gap-1.5 text-[11px] text-fg-tertiary">
              <ImageUp className="h-3 w-3" />
              {imgSrcs.length > 1
                ? "Картинки — выбери, какую заменить"
                : "Картинка — загрузи свою"}
            </div>
            {imgSrcs.length > 1 ? (
              <div className="flex gap-1.5 overflow-x-auto pb-1">
                {imgSrcs.map((s, i) => (
                  <button
                    key={s + i}
                    type="button"
                    onClick={() => setChosenSrc(s)}
                    title={`Картинка ${i + 1}`}
                    className={cn(
                      "relative h-12 w-12 shrink-0 rounded-md overflow-hidden border-2 transition-colors",
                      s === activeSrc
                        ? "border-accent"
                        : "border-transparent opacity-60 hover:opacity-100",
                    )}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={s} alt="" className="h-full w-full object-cover" />
                  </button>
                ))}
              </div>
            ) : null}
            <label
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                const f = e.dataTransfer.files?.[0];
                if (f) void replaceImage(f);
              }}
              className={cn(
                "relative block rounded-lg border border-dashed overflow-hidden cursor-pointer transition-colors",
                dragOver ? "border-accent" : "border-border-default",
              )}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={activeSrc} alt="" className="w-full h-24 object-cover" />
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                disabled={uploading}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void replaceImage(f);
                  e.target.value = "";
                }}
                className="absolute inset-0 opacity-0 cursor-pointer"
              />
              <div className="absolute inset-0 flex items-center justify-center bg-black/45 opacity-0 hover:opacity-100 transition-opacity text-[11px] text-white font-medium px-2 text-center">
                {uploading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : imgSrcs.length > 1 ? (
                  "Заменить выбранную — клик или перетащи"
                ) : (
                  "Заменить — клик или перетащи файл"
                )}
              </div>
            </label>
            <p className="text-[10px] text-fg-tertiary">
              PNG / JPG / WebP, до 6 МБ. Сохранится новой версией в истории.
            </p>
          </div>
        ) : null}

        {/* COLOR */}
        <div className="space-y-2">
          <div className="flex items-center rounded-lg border border-border-subtle bg-surface-raised p-0.5">
            {TARGETS.map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => setTarget(t.key)}
                className={cn(
                  "flex-1 h-6 rounded-md text-[11px] font-medium transition-colors",
                  target === t.key
                    ? "bg-accent-subtle text-accent"
                    : "text-fg-tertiary hover:text-fg-secondary",
                )}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <label
              className="relative h-8 w-8 rounded-md border border-border-default overflow-hidden cursor-pointer shrink-0"
              style={{ backgroundColor: currentHex }}
              title="Выбрать цвет"
            >
              <input
                type="color"
                value={currentHex}
                onChange={(e) => applyColor(e.target.value)}
                className="absolute inset-0 opacity-0 cursor-pointer"
              />
            </label>
            <span className="font-mono text-xs text-fg-secondary flex-1">
              {currentHex}
            </span>
            {hasEyeDropper && (
              <Button
                size="sm"
                variant="secondary"
                onClick={pickEyedropper}
                className="h-8 px-2 gap-1"
                title="Пипетка — взять цвет с экрана"
              >
                <Pipette className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>

          <label className="flex items-center gap-2 text-[11px] text-fg-tertiary cursor-pointer">
            <input
              type="checkbox"
              checked={siteWide}
              onChange={(e) => setSiteWide(e.target.checked)}
              className="accent-[var(--color-accent,#7c5cff)]"
            />
            <Globe className="h-3 w-3" />
            Применить ко всему сайту
          </label>
          {siteWide && (
            <select
              value={token}
              onChange={(e) => setTokenSel(e.target.value)}
              className="w-full rounded-md border border-border-default bg-surface-input px-2 py-1 text-xs text-fg-primary focus:outline-none"
            >
              {SITE_TOKENS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* FONT */}
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-[11px] text-fg-tertiary">
            <Type className="h-3 w-3" />
            Шрифт · сейчас{" "}
            <span className="text-fg-secondary font-medium">{activeFamily}</span>
          </div>
          <div className="max-h-44 overflow-y-auto scrollbar-elegant rounded-lg border border-border-subtle divide-y divide-border-subtle">
            {(fonts ?? []).map((f) => {
              const active = activeFamily === f.family;
              return (
                <button
                  key={f.family}
                  type="button"
                  onClick={() => applyFont(f)}
                  style={{ fontFamily: f.css_stack }}
                  className={cn(
                    "w-full flex items-center gap-2 px-3 py-2 text-left text-sm transition-colors",
                    active
                      ? "bg-accent-subtle text-fg-primary"
                      : "text-fg-secondary hover:bg-surface-raised hover:text-fg-primary",
                  )}
                >
                  <span className="flex-1 min-w-0 truncate">{f.family}</span>
                  <span className="text-[10px] font-mono text-fg-tertiary shrink-0">
                    {f.category}
                  </span>
                  {active && <Check className="h-3.5 w-3.5 text-accent shrink-0" />}
                </button>
              );
            })}
          </div>
        </div>

        {/* DELETE — remove (hide) any element from the page */}
        <div className="pt-1 border-t border-border-subtle">
          <Button
            size="sm"
            variant="ghost"
            onClick={deleteElement}
            disabled={deleting}
            title="Убрать этот элемент со страницы"
            className="w-full gap-1.5 text-fg-tertiary hover:text-red-400 hover:bg-red-500/10"
          >
            {deleting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="h-3.5 w-3.5" />
            )}
            Убрать элемент
          </Button>
        </div>
      </div>

      <div className="flex items-center gap-2 px-3 py-2.5 border-t border-border-subtle shrink-0">
        <Button
          size="sm"
          variant="ghost"
          onClick={reset}
          className="gap-1.5"
          title="Сбросить изменения"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          Сбросить
        </Button>
        <Button
          size="sm"
          onClick={save}
          disabled={!dirty || saving}
          className="ml-auto gap-1.5 rounded-full px-4"
        >
          {saving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Check className="h-3.5 w-3.5" />
          )}
          Сохранить
        </Button>
      </div>
    </div>
  );
}
