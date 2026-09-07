"use client";

import { useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FileCheck2, Loader2, Settings2, CircleAlert, Circle } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError } from "@/lib/api/client";
import {
  getMaxProjectConfig,
  saveMaxProjectConfig,
} from "@/lib/api/max-studio";
import type { MaxProjectConfig, MaxProjectConfigPayload } from "@/lib/api/types";
import { MAX_BRIEF_LENGTH } from "@/lib/max-brief";
import { cn } from "@/lib/utils";
import "./max-studio.css";
import "./max-project-workspace.css";
import "./max-project-setup.css";
import { MaxProjectSetupSections, type SetupSection } from "./MaxProjectSetupSections";
import { MaxProjectDataApplyDialog } from "./MaxProjectDataApplyDialog";

const SETUP_SECTIONS: Array<{ id: SetupSection; label: string }> = [
  { id: "details", label: "Основное" },
  { id: "content", label: "Контент" },
  { id: "owner", label: "Владелец" },
  { id: "policies", label: "Политики" },
];

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.message : "Попробуйте ещё раз";
}

export function MaxProjectSetupDialog({
  projectId,
  display = "panel",
  emphasized = false,
  label = "Данные приложения",
}: {
  projectId: string;
  display?: "panel" | "toolbar";
  emphasized?: boolean;
  label?: string;
}) {
  const qc = useQueryClient();
  const tabsId = useId();
  const scrollRegion = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<MaxProjectConfigPayload | null>(null);
  const [applyConfig, setApplyConfig] = useState<MaxProjectConfig | null>(null);
  const applyAfterSave = useRef(false);
  const [section, setSection] = useState<SetupSection>("details");
  const config = useQuery({
    queryKey: ["max-config", projectId],
    queryFn: () => getMaxProjectConfig(projectId),
    enabled: open,
  });

  const current = draft ?? config.data?.config ?? null;
  const pendingApplication = config.data?.application_mode === "runtime"
    && config.data.config_version > 0 && !config.data.synced_snapshot_id;

  const save = useMutation({
    mutationFn: (payload: MaxProjectConfigPayload) =>
      saveMaxProjectConfig(projectId, payload),
    onSuccess: (data) => {
      qc.setQueryData(["max-config", projectId], data);
      void qc.invalidateQueries({ queryKey: ["max-readiness", projectId] });
      void qc.invalidateQueries({ queryKey: ["snapshots", projectId] });
      void qc.invalidateQueries({ queryKey: ["max-preview-session", projectId] });
      toast.success("Данные сохранены", {
        description: data.application_mode === "runtime"
          ? (data.synced_snapshot_id
            ? "Конфигурация, поддержка и документы обновлены. Для изменения экранов нажмите «Применить к приложению»."
            : "Они будут использованы при создании приложения.")
          : "Настройки и документы сохранены в версии кода. Для изменения экранов используйте «Применить к приложению», затем опубликуйте результат.",
      });
      if (applyAfterSave.current) setApplyConfig(data);
      applyAfterSave.current = false;
      setOpen(false);
      setDraft(null);
    },
    onError: (error) => {
      applyAfterSave.current = false;
      toast.error("Не удалось сохранить", { description: errorMessage(error) });
    },
  });
  const saved = config.data?.config;
  const changedSections = current && saved
    ? [
        JSON.stringify({
          app_name: current.app_name,
          app_type: current.app_type,
          summary: current.summary,
          audience: current.audience,
          primary_action: current.primary_action,
          features: current.features,
          style: current.style,
          brand_colors: current.brand_colors,
        }) !==
          JSON.stringify({
            app_name: saved.app_name,
            app_type: saved.app_type,
            summary: saved.summary,
            audience: saved.audience,
            primary_action: saved.primary_action,
            features: saved.features,
            style: saved.style,
            brand_colors: saved.brand_colors,
          }),
        JSON.stringify(current.content) !== JSON.stringify(saved.content),
        JSON.stringify({ operator: current.operator, support: current.support }) !==
          JSON.stringify({ operator: saved.operator, support: saved.support }),
        JSON.stringify(current.legal) !== JSON.stringify(saved.legal),
      ].filter(Boolean).length
    : 0;

  const saveIssue = current && (!current.app_name.trim() || !current.summary.trim())
    ? "Для сохранения заполните название и описание в разделе «Основное»."
    : current && Array.from(current.summary.trim()).length > MAX_BRIEF_LENGTH
      ? "Сократите описание в разделе «Основное» до 20 000 символов."
      : null;
  const selectSection = (next: SetupSection) => {
    setSection(next);
    if (scrollRegion.current) scrollRegion.current.scrollTop = 0;
  };

  return (
    <>
      <Button
        size="sm"
        variant={emphasized ? "primary" : "secondary"}
        className={
          display === "panel"
            ? cn(
                "h-11 min-w-0 w-full gap-1.5 overflow-hidden rounded-lg px-2 text-[11px]",
                !emphasized &&
                  "border-border-default bg-surface text-fg-secondary hover:bg-surface-base",
              )
            : "h-11 gap-1.5 px-2.5 text-xs sm:h-7"
        }
        onClick={() => {
          setSection("details");
          setDraft(null);
          setOpen(true);
        }}
        data-testid="max-settings-open"
      >
        <Settings2 className="h-3.5 w-3.5" />
        <span className="min-w-0 truncate">
          {display === "panel" ? label : "Настройки"}
        </span>
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setDraft(null);
        }}
      >
        <DialogContent
          data-product-shell
          data-max-studio
          className="max-settings-dialog max-project-setup"
        >
          <DialogHeader className="max-setup-header">
            <DialogTitle className="max-setup-title">
              <FileCheck2 className="h-5 w-5 text-accent" />
              Данные приложения
            </DialogTitle>
            <DialogDescription className="max-setup-description">
              Сохраните данные, контакты и правила. Чтобы изменить экраны и функции готового приложения, примените данные с помощью ИИ.
            </DialogDescription>
          </DialogHeader>

          <div className="max-setup-tabs">
            <div role="tablist" aria-label="Разделы данных приложения">
              {SETUP_SECTIONS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  id={`${tabsId}-${item.id}`}
                  aria-controls={`${tabsId}-panel`}
                  aria-selected={section === item.id}
                  tabIndex={section === item.id ? 0 : -1}
                  onClick={() => selectSection(item.id)}
                  onKeyDown={(event) => {
                    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                    event.preventDefault();
                    const index = SETUP_SECTIONS.findIndex(tab => tab.id === item.id);
                    const next = event.key === "Home" ? 0 : event.key === "End" ? SETUP_SECTIONS.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + SETUP_SECTIONS.length) % SETUP_SECTIONS.length;
                    selectSection(SETUP_SECTIONS[next].id);
                    document.getElementById(`${tabsId}-${SETUP_SECTIONS[next].id}`)?.focus();
                  }}
                  className="max-setup-tab"
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          {config.isError ? (
            <div role="alert" className="p-6 text-sm"><p>Не удалось загрузить данные приложения.</p><Button variant="outline" className="mt-3" onClick={() => void config.refetch()}>Повторить загрузку</Button></div>
          ) : config.isLoading || !current ? (
            <div className="flex min-h-44 flex-1 items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-accent" />
            </div>
          ) : (
            <>
              <div
                className="max-setup-body"
                ref={scrollRegion}
                role="tabpanel"
                id={`${tabsId}-panel`}
                aria-labelledby={`${tabsId}-${section}`}
                data-testid="max-settings-scroll-region"
              >
                <MaxProjectSetupSections key={section} section={section} current={current} onChange={setDraft} />
              </div>

              <div
                className="max-setup-footer"
                data-testid="max-settings-footer"
              >
                <p id={`${tabsId}-save-status`} className="max-setup-save-status" role="status" data-state={saveIssue ? "invalid" : changedSections ? "changed" : "saved"}>
                  {saveIssue ? <CircleAlert aria-hidden="true" /> : changedSections ? <Circle aria-hidden="true" /> : <Check aria-hidden="true" />}
                  <span>{saveIssue ?? (save.isPending ? "Сохраняем изменения…" : changedSections > 0
                    ? `Есть несохранённые изменения · разделов: ${changedSections}`
                    : pendingApplication ? "Настройки сохранены. Их можно применить к приложению." : "Все изменения сохранены")}</span>
                </p>
                <Button
                  className="max-setup-save"
                  aria-describedby={`${tabsId}-save-status`}
                  disabled={
                    save.isPending ||
                    (changedSections === 0 && !pendingApplication) ||
                    Boolean(saveIssue)
                  }
                  onClick={() => { applyAfterSave.current = false; save.mutate(current); }}
                >
                  {save.isPending && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  Сохранить и проверить
                </Button>
                <Button className="max-setup-save" disabled={save.isPending || Boolean(saveIssue)} onClick={() => {
                  if (changedSections || pendingApplication || !config.data?.config_version || config.data.application_mode !== "runtime") {
                    applyAfterSave.current = true;
                    save.mutate(current);
                  } else if (config.data) {
                    setOpen(false);
                    setApplyConfig(config.data);
                  }
                }}>{changedSections ? "Сохранить и применить" : "Применить к приложению"}</Button>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
      {applyConfig && <MaxProjectDataApplyDialog config={applyConfig} onClose={() => setApplyConfig(null)} />}
    </>
  );
}
