"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Clapperboard,
  ExternalLink,
  ImagePlus,
  Loader2,
  RefreshCw,
  Upload,
  X,
} from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { cn, formatRelativeTime } from "@/lib/utils";
import type {
  HeroMediaFocusPreference,
  HeroMediaMotionPreference,
  HeroMediaPlanKind,
  Project,
} from "@/lib/api/types";
import {
  applyHeroMediaRender,
  approveHeroMediaPlan,
  createHeroMediaPlan,
  createHeroMediaRender,
  getHeroMediaRender,
  heroMediaPreviewUrl,
  listHeroMediaAssets,
  listHeroMediaPlans,
  listHeroMediaRenders,
  retryHeroMediaRender,
  uploadHeroMediaAsset,
} from "@/lib/api/heroMedia";

const PLAN_OPTIONS: Array<{
  value: HeroMediaPlanKind;
  label: string;
  hint: string;
}> = [
  {
    value: "static",
    label: "Static",
    hint: "Сильная фотография или clean visual без движения.",
  },
  {
    value: "product-demo",
    label: "Product demo",
    hint: "Интерфейс, продуктовый сценарий или предмет крупным планом.",
  },
  {
    value: "motion",
    label: "Motion",
    hint: "Тонкая кинетика, depth и микро-движение без видео.",
  },
  {
    value: "video",
    label: "Video",
    hint: "Короткий ролик, только если он реально усиливает hero.",
  },
  {
    value: "cinematic",
    label: "Cinematic",
    hint: "Самый выразительный режим для брендовых и атмосферных сцен.",
  },
];

const FOCUS_OPTIONS: Array<{
  value: HeroMediaFocusPreference;
  label: string;
}> = [
  { value: "auto", label: "Реши сам" },
  { value: "product", label: "Товар" },
  { value: "interface", label: "Интерфейс" },
  { value: "atmosphere", label: "Атмосфера" },
  { value: "result", label: "Результат" },
];

const MOTION_OPTIONS: Array<{
  value: HeroMediaMotionPreference;
  label: string;
}> = [
  { value: "auto", label: "Реши сам" },
  { value: "calm", label: "Спокойный" },
  { value: "lively", label: "Живой" },
  { value: "cinematic", label: "Кинематографичный" },
];

function isStaticTemplate(template: Project["template"]) {
  return (
    template === "blank" ||
    template === "landing" ||
    template === "portfolio" ||
    template === "blog"
  );
}

function planBadgeVariant(plan: HeroMediaPlanKind) {
  if (plan === "static" || plan === "product-demo") return "default" as const;
  if (plan === "motion") return "accent" as const;
  if (plan === "video") return "warning" as const;
  return "success" as const;
}

function planLabel(plan: HeroMediaPlanKind) {
  return PLAN_OPTIONS.find((option) => option.value === plan)?.label ?? plan;
}

function renderBadgeVariant(status: string) {
  if (status === "completed") return "success" as const;
  if (status === "failed") return "danger" as const;
  if (status === "queued") return "default" as const;
  return "accent" as const;
}

const RENDER_STATUS_LABELS: Record<string, string> = {
  queued: "В очереди",
  rendering: "Генерация",
  assembling: "Сборка",
  completed: "Готово",
  failed: "Нужен повтор",
};

export function HeroMediaPanel({
  open,
  project,
  onClose,
  onApplied,
}: {
  open: boolean;
  project: Project;
  onClose: () => void;
  onApplied?: () => void;
}) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);

  const [consentConfirmed, setConsentConfirmed] = useState(false);
  const [promptDraft, setPrompt] = useState<string | null>(null);
  const [businessTypeDraft, setBusinessType] = useState<string | null>(null);
  const [stylePreferenceDraft, setStylePreference] = useState<string | null>(
    null,
  );
  const [focusPreferenceDraft, setFocusPreference] =
    useState<HeroMediaFocusPreference | null>(null);
  const [motionPreferenceDraft, setMotionPreference] =
    useState<HeroMediaMotionPreference | null>(null);
  const [selectedAssetIdsDraft, setSelectedAssetIds] = useState<
    string[] | null
  >(null);
  const [selectedPlanKind, setSelectedPlanKind] =
    useState<HeroMediaPlanKind | null>(null);
  const [showSetup, setShowSetup] = useState(false);

  const { data: assets = [] } = useQuery({
    queryKey: ["hero-media-assets", project.id],
    queryFn: () => listHeroMediaAssets(project.id),
    enabled: open,
  });
  const { data: plans = [] } = useQuery({
    queryKey: ["hero-media-plans", project.id],
    queryFn: () => listHeroMediaPlans(project.id),
    enabled: open,
  });
  const { data: renders = [] } = useQuery({
    queryKey: ["hero-media-renders", project.id],
    queryFn: () => listHeroMediaRenders(project.id),
    enabled: open,
  });

  const latestPlan = plans[0] ?? null;
  const latestRenderStub = renders[0] ?? null;
  const latestRenderId = latestRenderStub?.id ?? null;
  const prompt = promptDraft ?? latestPlan?.input_prompt ?? "";
  const businessType =
    businessTypeDraft ?? latestPlan?.business_type ?? "";
  const stylePreference =
    stylePreferenceDraft ?? latestPlan?.style_preference ?? "";
  const focusPreference =
    focusPreferenceDraft ??
    FOCUS_OPTIONS.find(
      (option) => option.value === latestPlan?.focus_preference,
    )?.value ??
    "auto";
  const motionPreference =
    motionPreferenceDraft ??
    MOTION_OPTIONS.find(
      (option) => option.value === latestPlan?.motion_preference,
    )?.value ??
    "auto";
  const selectedAssetIds =
    selectedAssetIdsDraft ?? latestPlan?.asset_ids ?? [];

  const { data: activeRender } = useQuery({
    queryKey: ["hero-media-render", project.id, latestRenderId],
    queryFn: () => getHeroMediaRender(project.id, latestRenderId!),
    initialData: latestRenderStub,
    enabled: open && !!latestRenderId,
    refetchInterval: (query) => {
      const current = query.state.data?.status;
      return current === "queued" ||
        current === "rendering" ||
        current === "assembling"
        ? 2000
        : false;
    },
  });

  const effectivePlanKind =
    selectedPlanKind ??
    latestPlan?.selected_plan_kind ??
    latestPlan?.recommended_plan_kind ??
    "motion";

  const uploadMut = useMutation({
    mutationFn: async (files: File[]) => {
      const uploaded = [];
      for (const file of files) {
        uploaded.push(
          await uploadHeroMediaAsset(project.id, file, {
            consentConfirmed,
            filename: file.name,
          }),
        );
      }
      return uploaded;
    },
    onSuccess: (uploaded) => {
      qc.invalidateQueries({ queryKey: ["hero-media-assets", project.id] });
      setSelectedAssetIds((prev) => {
        const current = prev ?? latestPlan?.asset_ids ?? [];
        return [
          ...new Set([...current, ...uploaded.map((asset) => asset.id)]),
        ];
      });
      toast.success("Фотографии загружены");
    },
    onError: (error) => {
      toast.error("Не удалось загрузить фото", {
        description: error instanceof Error ? error.message : undefined,
      });
    },
  });

  const planMut = useMutation({
    mutationFn: () =>
      createHeroMediaPlan(project.id, {
        prompt: prompt.trim(),
        business_type: businessType.trim() || null,
        style_preference: stylePreference.trim() || null,
        focus_preference: focusPreference,
        motion_preference: motionPreference,
        asset_ids: selectedAssetIds,
      }),
    onSuccess: (plan) => {
      qc.invalidateQueries({ queryKey: ["hero-media-plans", project.id] });
      setSelectedPlanKind(plan.recommended_plan_kind);
      toast.success("План подачи собран");
    },
    onError: (error) => {
      toast.error("Не удалось собрать план", {
        description: error instanceof Error ? error.message : undefined,
      });
    },
  });

  const approveMut = useMutation({
    mutationFn: () =>
      approveHeroMediaPlan(project.id, latestPlan!.id, effectivePlanKind),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hero-media-plans", project.id] });
      toast.success("План подтверждён");
    },
    onError: (error) => {
      toast.error("Не удалось подтвердить план", {
        description: error instanceof Error ? error.message : undefined,
      });
    },
  });

  const renderMut = useMutation({
    mutationFn: () => createHeroMediaRender(project.id, latestPlan!.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hero-media-renders", project.id] });
      toast.success("Hero поставлен в очередь");
    },
    onError: (error) => {
      toast.error("Не удалось запустить рендер", {
        description: error instanceof Error ? error.message : undefined,
      });
    },
  });

  const retryMut = useMutation({
    mutationFn: () => retryHeroMediaRender(project.id, activeRender!.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hero-media-renders", project.id] });
      toast.success("Рендер отправлен на повтор");
    },
    onError: (error) => {
      toast.error("Не удалось повторить рендер", {
        description: error instanceof Error ? error.message : undefined,
      });
    },
  });

  const applyMut = useMutation({
    mutationFn: () => applyHeroMediaRender(project.id, activeRender!.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["snapshots", project.id] });
      qc.invalidateQueries({ queryKey: ["hero-media-renders", project.id] });
      toast.success("Hero применён в текущую версию сайта");
      onApplied?.();
    },
    onError: (error) => {
      toast.error("Не удалось применить hero", {
        description: error instanceof Error ? error.message : undefined,
      });
    },
  });

  const planApproved = latestPlan?.status === "approved";
  const hasCompletedResult =
    activeRender?.status === "completed" && !!activeRender.bundle;
  const canApply =
    !!activeRender?.bundle && !activeRender.applied_snapshot_id;
  const previewUrl = activeRender
    ? heroMediaPreviewUrl(project.id, activeRender.id)
    : null;

  if (!open) return null;

  return (
    <AnimatePresence>
      <motion.aside
        initial={{ opacity: 0, x: 24 }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, x: 24 }}
        transition={{ duration: 0.22 }}
        data-testid="hero-media-panel"
        className="absolute inset-y-2 right-2 z-20 w-[390px] max-w-[calc(100%-16px)] overflow-hidden rounded-xl border border-border-default bg-surface-raised/96 shadow-[0_20px_60px_rgba(0,0,0,0.35)] backdrop-blur"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-start justify-between gap-3 border-b border-border-subtle px-4 py-4">
            <div className="space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="accent">Hero media</Badge>
                {activeRender ? (
                  <Badge variant={renderBadgeVariant(activeRender.status)}>
                    {activeRender.applied_snapshot_id
                      ? "Применено"
                      : (RENDER_STATUS_LABELS[activeRender.status] ??
                        activeRender.status)}
                  </Badge>
                ) : (
                  <span className="text-[11px] text-fg-secondary">
                    один сильный экран
                  </span>
                )}
              </div>
              <div className="text-sm font-medium text-fg-primary">
                Hero из фото и обычного брифа
              </div>
              {hasCompletedResult && !showSetup ? (
                <button
                  type="button"
                  onClick={() => setShowSetup(true)}
                  className="text-xs font-medium text-accent hover:text-accent-hover"
                >
                  Изменить исходные фото или бриф
                </button>
              ) : (
                <div className="text-xs leading-5 text-fg-secondary">
                  Видео не включается автоматически: сначала система рекомендует
                  формат подачи, потом вы подтверждаете или меняете его.
                </div>
              )}
            </div>
            <Button
              size="icon"
              variant="ghost"
              aria-label="Закрыть Hero media"
              onClick={onClose}
            >
              <X className="h-4 w-4" />
            </Button>
          </div>

          <div className="flex-1 space-y-5 overflow-y-auto p-4 scrollbar-elegant">
            {hasCompletedResult && showSetup && (
              <Button
                size="sm"
                variant="ghost"
                className="w-full"
                onClick={() => setShowSetup(false)}
              >
                Вернуться к готовому hero
              </Button>
            )}
            {(!hasCompletedResult || showSetup) && (
              <>
                <section className="space-y-3">
                  <div className="text-xs font-medium uppercase tracking-[0.14em] text-fg-secondary">
                    1. Исходные фото
                  </div>
              <label className="flex items-start gap-2 rounded-lg border border-border-default bg-surface-base/60 px-3 py-3 text-xs leading-5 text-fg-secondary">
                <input
                  type="checkbox"
                  data-testid="hero-media-consent"
                  checked={consentConfirmed}
                  onChange={(e) => setConsentConfirmed(e.target.checked)}
                  className="mt-0.5"
                />
                <span>
                  Подтверждаю, что у меня есть право использовать эти фото в
                  генерации первого экрана.
                </span>
              </label>

              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                multiple
                data-testid="hero-media-upload-input"
                className="hidden"
                onChange={(e) => {
                  const files = Array.from(e.target.files ?? []);
                  if (!files.length) return;
                  if (!consentConfirmed) {
                    toast.error("Сначала подтвердите право на использование фото");
                    e.currentTarget.value = "";
                    return;
                  }
                  uploadMut.mutate(files);
                  e.currentTarget.value = "";
                }}
              />
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                className={cn(
                  "w-full rounded-xl border border-dashed border-border-strong bg-surface-base/40 px-4 py-4 text-left transition-colors",
                  "hover:border-accent/50 hover:bg-accent-subtle/20",
                )}
              >
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent-subtle text-accent">
                    {uploadMut.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Upload className="h-4 w-4" />
                    )}
                  </div>
                  <div className="space-y-1">
                    <div className="text-sm font-medium text-fg-primary">
                      Загрузить фотографии
                    </div>
                    <div className="text-xs leading-5 text-fg-secondary">
                      Лучше 2–6 сильных кадров, чем большая россыпь слабых.
                    </div>
                  </div>
                </div>
              </button>

              {assets.length > 0 && (
                <div className="grid grid-cols-3 gap-2">
                  {assets.map((asset) => {
                    const selected = selectedAssetIds.includes(asset.id);
                    return (
                      <button
                        key={asset.id}
                        type="button"
                        onClick={() =>
                          setSelectedAssetIds((prev) => {
                            const current =
                              prev ?? latestPlan?.asset_ids ?? [];
                            return current.includes(asset.id)
                              ? current.filter((id) => id !== asset.id)
                              : [...current, asset.id];
                          })
                        }
                        className={cn(
                          "relative overflow-hidden rounded-lg border bg-surface-base",
                          selected
                            ? "border-accent ring-1 ring-accent/40"
                            : "border-border-default",
                        )}
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={asset.storage_url}
                          alt={asset.original_filename ?? "Uploaded source"}
                          className="aspect-[4/3] w-full object-cover"
                        />
                        <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/60 to-transparent px-2 py-1 text-[10px] text-white">
                          {selected ? "в плане" : "источник"}
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
                </section>

                <section className="space-y-3">
                  <div className="text-xs font-medium uppercase tracking-[0.14em] text-fg-secondary">
                    2. Что важно показать
                  </div>
              <Textarea
                data-testid="hero-media-prompt"
                aria-label="Что важно показать в первом экране"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Например: хочу показать фактуру бутылки, цвет жидкости и ощущение дорогого вечернего света."
                className="min-h-[96px]"
              />
              <input
                data-testid="hero-media-business-type"
                aria-label="Тип продукта или бизнеса"
                value={businessType}
                onChange={(e) => setBusinessType(e.target.value)}
                placeholder="Тип продукта или бизнеса"
                className="h-9 w-full rounded-md border border-border-default bg-surface-input px-3 text-sm text-fg-primary placeholder:text-fg-tertiary"
              />
              <input
                data-testid="hero-media-style"
                aria-label="Стиль бренда или настроение"
                value={stylePreference}
                onChange={(e) => setStylePreference(e.target.value)}
                placeholder="Стиль бренда или mood"
                className="h-9 w-full rounded-md border border-border-default bg-surface-input px-3 text-sm text-fg-primary placeholder:text-fg-tertiary"
              />

              <div className="space-y-2">
                <div className="text-xs text-fg-secondary">
                  Что показать главным?
                </div>
                <div className="flex flex-wrap gap-2">
                  {FOCUS_OPTIONS.map((option) => (
                    <Button
                      key={option.value}
                      data-testid={`hero-media-focus-${option.value}`}
                      size="sm"
                      variant={
                        focusPreference === option.value
                          ? "pill-primary"
                          : "pill-secondary"
                      }
                      onClick={() => setFocusPreference(option.value)}
                    >
                      {option.label}
                    </Button>
                  ))}
                </div>
              </div>

              <div className="space-y-2">
                <div className="text-xs text-fg-secondary">
                  Какой характер нужен?
                </div>
                <div className="flex flex-wrap gap-2">
                  {MOTION_OPTIONS.map((option) => (
                    <Button
                      key={option.value}
                      data-testid={`hero-media-motion-${option.value}`}
                      size="sm"
                      variant={
                        motionPreference === option.value
                          ? "pill-primary"
                          : "pill-secondary"
                      }
                      onClick={() => setMotionPreference(option.value)}
                    >
                      {option.label}
                    </Button>
                  ))}
                </div>
              </div>

              <Button
                data-testid="hero-media-build-plan"
                variant="primary"
                className="w-full"
                disabled={!prompt.trim() || planMut.isPending}
                onClick={() => planMut.mutate()}
              >
                {planMut.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Clapperboard className="h-4 w-4" />
                )}
                Собрать рекомендацию
              </Button>
                </section>
              </>
            )}

            {latestPlan && (
              <section className="space-y-3 border-t border-border-subtle pt-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="text-xs font-medium uppercase tracking-[0.14em] text-fg-secondary">
                    3. Рекомендация
                  </div>
                  <Badge variant={planBadgeVariant(latestPlan.recommended_plan_kind)}>
                    {planLabel(latestPlan.recommended_plan_kind)}
                  </Badge>
                </div>
                <div className="rounded-xl border border-border-default bg-surface-base/50 p-3 space-y-2">
                  <div className="text-sm font-medium text-fg-primary">
                    {latestPlan.plan.hero_headline}
                  </div>
                  <div className="text-sm leading-6 text-fg-secondary">
                    {latestPlan.plan.explanation}
                  </div>
                  <div className="grid gap-1 text-xs text-fg-tertiary">
                    <div>Фокус: {latestPlan.plan.recommended_focus}</div>
                    <div>Тон: {latestPlan.plan.recommended_tone}</div>
                    <div>Бренд: {latestPlan.plan.brand_fit_note}</div>
                    <div>Скорость: {latestPlan.plan.performance_note}</div>
                  </div>
                </div>

                {(!hasCompletedResult || showSetup) && (
                  <>
                    <div className="grid gap-2">
                      {PLAN_OPTIONS.map((option) => (
                        <button
                          key={option.value}
                          type="button"
                          data-testid={`hero-media-plan-option-${option.value}`}
                          onClick={() => setSelectedPlanKind(option.value)}
                          className={cn(
                            "rounded-xl border px-3 py-3 text-left transition-colors",
                            effectivePlanKind === option.value
                              ? "border-accent bg-accent-subtle/25"
                              : "border-border-default bg-surface-base/25 hover:border-border-strong",
                          )}
                        >
                          <div className="flex items-center justify-between gap-3">
                            <div>
                              <div className="text-sm font-medium text-fg-primary">
                                {option.label}
                              </div>
                              <div className="mt-1 text-xs leading-5 text-fg-secondary">
                                {option.hint}
                              </div>
                            </div>
                            {effectivePlanKind === option.value && (
                              <Check className="h-4 w-4 shrink-0 text-accent" />
                            )}
                          </div>
                        </button>
                      ))}
                    </div>

                    {latestPlan.plan.storyboard.length > 0 && (
                      <div className="space-y-2 rounded-xl border border-border-default bg-surface-base/35 p-3">
                        <div className="text-xs font-medium uppercase tracking-[0.14em] text-fg-secondary">
                          Раскадровка
                        </div>
                        {latestPlan.plan.storyboard.map((shot) => (
                          <div key={shot.label} className="space-y-1">
                            <div className="text-sm font-medium text-fg-primary">
                              {shot.label}
                            </div>
                            <div className="text-xs leading-5 text-fg-secondary">
                              {shot.purpose}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    <Button
                      data-testid="hero-media-approve-plan"
                      variant="primary"
                      className="w-full"
                      disabled={approveMut.isPending}
                      onClick={() => approveMut.mutate()}
                    >
                      {approveMut.isPending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Check className="h-4 w-4" />
                      )}
                      Подтвердить этот план
                    </Button>
                  </>
                )}
              </section>
            )}

            {planApproved && (
              <section className="space-y-3 border-t border-border-subtle pt-4">
                <div className="text-xs font-medium uppercase tracking-[0.14em] text-fg-secondary">
                  4. Сборка и preview
                </div>

                {!activeRender && (
                  <Button
                    data-testid="hero-media-render"
                    variant="primary"
                    className="w-full"
                    disabled={renderMut.isPending}
                    onClick={() => renderMut.mutate()}
                  >
                    {renderMut.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <ImagePlus className="h-4 w-4" />
                    )}
                    Собрать hero
                  </Button>
                )}

                {activeRender && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between gap-3">
                      <Badge variant={renderBadgeVariant(activeRender.status)}>
                        {activeRender.applied_snapshot_id
                          ? "Применено"
                          : (RENDER_STATUS_LABELS[activeRender.status] ??
                            activeRender.status)}
                      </Badge>
                      <div className="text-[11px] text-fg-secondary">
                        Попытки: {activeRender.retry_count + 1}
                      </div>
                    </div>

                    {activeRender.status_detail && (
                      <div className="rounded-lg border border-border-default bg-surface-base/40 px-3 py-2 text-sm text-fg-secondary">
                        {activeRender.status_detail}
                      </div>
                    )}

                    {activeRender.progress_log.length > 0 && (
                      <div className="rounded-lg border border-border-default bg-surface-base/30 px-3 py-3 space-y-2">
                        {activeRender.progress_log.slice(-6).map((entry, index) => (
                          <div
                            key={`${entry.at}-${index}`}
                            className="flex items-start justify-between gap-3 text-xs"
                          >
                            <div>
                              <div className="text-fg-primary">{entry.detail}</div>
                              <div className="text-fg-secondary">
                                {RENDER_STATUS_LABELS[entry.status] ?? entry.status}
                              </div>
                            </div>
                            <div className="whitespace-nowrap text-fg-secondary">
                              {formatRelativeTime(entry.at)}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    {(activeRender.status === "failed" ||
                      activeRender.status === "completed") && (
                      <Button
                        data-testid="hero-media-retry-render"
                        variant="secondary"
                        className="w-full"
                        disabled={retryMut.isPending}
                        onClick={() => retryMut.mutate()}
                      >
                        {retryMut.isPending ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <RefreshCw className="h-4 w-4" />
                        )}
                        {activeRender.status === "completed"
                          ? "Пересобрать hero"
                          : "Повторить рендер"}
                      </Button>
                    )}

                    {activeRender.bundle && (
                      <div className="space-y-3">
                        <div className="flex items-center justify-between gap-3">
                          <div>
                            <div className="text-xs font-medium text-fg-primary">
                              Быстрый preview
                            </div>
                            <div className="text-[11px] text-fg-secondary">
                              Узкая версия проверяет mobile fallback
                            </div>
                          </div>
                          <Button size="sm" variant="ghost" asChild>
                            <a
                              href={previewUrl ?? undefined}
                              target="_blank"
                              rel="noreferrer"
                              aria-label="Открыть Hero media preview в новой вкладке"
                            >
                              <ExternalLink className="h-4 w-4" />
                              Открыть крупно
                            </a>
                          </Button>
                        </div>
                        <div className="overflow-hidden rounded-xl border border-border-default bg-surface-base">
                          <iframe
                            data-testid="hero-media-preview-frame"
                            title="Hero media preview"
                            src={previewUrl ?? undefined}
                            className="h-[320px] w-full border-0 bg-black"
                          />
                        </div>

                        {!isStaticTemplate(project.template) && (
                          <div className="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning">
                            Авто-применение в этой итерации сделано только для static
                            website templates. Preview уже честный.
                          </div>
                        )}

                        {activeRender.applied_snapshot_id ? (
                          <div className="flex items-center gap-2 rounded-lg border border-success/30 bg-success/10 px-3 py-2 text-xs leading-5 text-success">
                            <Check className="h-4 w-4" />
                            Hero уже применён в текущую версию сайта.
                          </div>
                        ) : (
                          <Button
                            data-testid="hero-media-apply"
                            variant="primary"
                            className="w-full"
                            disabled={!canApply || applyMut.isPending}
                            onClick={() => applyMut.mutate()}
                          >
                            {applyMut.isPending ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <ImagePlus className="h-4 w-4" />
                            )}
                            Применить hero в сайт
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </section>
            )}
          </div>
        </div>
      </motion.aside>
    </AnimatePresence>
  );
}
