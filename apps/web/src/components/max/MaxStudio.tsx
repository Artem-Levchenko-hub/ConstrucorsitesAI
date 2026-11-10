"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, FolderKanban, Plus, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { connectAppIntegration, getIntegrationCatalog } from "@/lib/api/app-integrations";
import { describeApiError } from "@/lib/api/errors";
import { saveLandingPrompt, takeLandingPrompt } from "@/lib/landing-prompt";
import { createProject, listProjects } from "@/lib/api/projects";
import type { ManagementModule, MaxContentItem } from "@/lib/api/types";
import { MAX_DEFAULT_SUMMARY, buildMaxProjectPrompt, defaultManagementModules, newProjectManagementModules, selectedMaxAppTypes, serializeMaxProjectFeatures, type MaxAppTypeId, type MaxFeature, type MaxProjectBrief, type MaxStyleId } from "@/lib/max-brief";
import { containsChatSecret, resolveChatCredential } from "@/lib/max-chat-credentials";
import { extractInitialCatalog } from "@/lib/max-catalog-brief";
import { newMaxContentItem } from "@/lib/max-catalog";
import { initialCatalogError, publicInitialCatalog, publicMaxBriefText } from "@/lib/max-initial-catalog";
import { useOwnerChatQuota } from "@/hooks/useOwnerChatQuota";
import { FREE_CHAT_UPGRADE_MESSAGE } from "@/lib/owner-chat-quota";
import { OwnerChatQuotaNotice } from "@/components/workspace/OwnerChatQuotaNotice";
import { useLearningGate } from "@/components/learning/LearningProvider";
import { MaxStudioProjectCard } from "./MaxStudioProjectCard";
import { MaxStudioHeader } from "./MaxStudioHeader";
import { MAX_STATUS_COPY } from "@/lib/max-status-copy";
import { MaxStudioIdeas, type MaxStudioIdea } from "./MaxStudioIdeas";
import { MaxProjectWizard, type WizardOrderSettings } from "./MaxProjectWizard";
import { MaxInitialCatalog } from "./MaxInitialCatalog";
import "./max-studio.css";

const STARTER_FEATURES: MaxFeature[] = ["Профиль пользователя", "История действий"];

export function MaxStudio({ email }: { email: string }) {
  const router = useRouter();
  const quota = useOwnerChatQuota();
  const qc = useQueryClient();
  const learning = useLearningGate();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [search, setSearch] = useState("");
  const searchField = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [idea, setIdea] = useState("");

  const [appTypes, setAppTypes] = useState<MaxAppTypeId[]>(["loyalty"]);
  const appType = appTypes[0];
  const [audience, setAudience] = useState("");
  const [primaryAction, setPrimaryAction] = useState("");
  const [features, setFeatures] = useState<MaxFeature[]>(STARTER_FEATURES);
  const [style, setStyle] = useState<MaxStyleId>("brand");
  const [brandColors, setBrandColors] = useState("");
  const [managementModules, setManagementModules] = useState<ManagementModule[]>(() => defaultManagementModules("loyalty"));
  const [orderSettings, setOrderSettings] = useState<WizardOrderSettings>({ mode: "pickup", prepMinutes: "7" });
  const [managementModuleOptOuts, setManagementModuleOptOuts] = useState<ManagementModule[]>([]);
  const safeCatalogIdea = useMemo(() => publicMaxBriefText(idea), [idea]);
  const proposedCatalog = useMemo(() => {
    const result = extractInitialCatalog(safeCatalogIdea);
    return { ...result, items: result.items.map(item => ({ ...newMaxContentItem(), ...item })) };
  }, [safeCatalogIdea]);
  const [catalogDraft, setCatalogDraft] = useState<{ source: string; items: MaxContentItem[]; tooManyItems: boolean } | null>(null);
  const initialContent = catalogDraft?.items ?? proposedCatalog.items;
  const tooManyItems = catalogDraft?.tooManyItems ?? proposedCatalog.tooManyItems;
  const catalogError = initialCatalogError(publicInitialCatalog(initialContent), tooManyItems);

  const projects = useQuery({ queryKey: learning ? ["projects", learning.userKey] : ["projects"], queryFn: listProjects });
  const allMaxProjects = useMemo(() => (projects.data ?? []).filter(project => project.template === "max_miniapp"), [projects.data]);
  // Открытие мастера переносит черновик один раз и не создаёт проект.
  useEffect(() => {
    const pending = takeLandingPrompt();
    if (!pending) return;
    let applied = false;
    const frame = window.requestAnimationFrame(() => {
      applied = true;
      setIdea(pending);
      setDialogOpen(true);
    });
    return () => {
      window.cancelAnimationFrame(frame);
      if (!applied) saveLandingPrompt(pending);
    };
  }, []);
  const maxProjects = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("ru-RU");
    return allMaxProjects.filter(project => !needle || project.name.toLocaleLowerCase("ru-RU").includes(needle));
  }, [allMaxProjects, search]);

  const create = useMutation({
    mutationFn: async () => {
      if (quota.initialExhausted) throw new Error(FREE_CHAT_UPGRADE_MESSAGE);
      const content = publicInitialCatalog(initialContent);
      const contentError = initialCatalogError(content, tooManyItems);
      if (contentError) throw new Error(contentError);
      const modules = newProjectManagementModules(appType, managementModules, appTypes);
      const hasOrders = modules.includes("orders");
      const prepMinutes = hasOrders ? Number(orderSettings.prepMinutes) : 7;
      if (!Number.isInteger(prepMinutes) || prepMinutes < 1 || prepMinutes > 120) {
        throw new Error("Укажите время приготовления от 1 до 120 минут.");
      }
      const rawPrompt = buildMaxProjectPrompt({
        name,
        idea,
        appType,
        appTypes,
        audience,
        primaryAction,
        features,
        style,
        brandColors,
        managementModules: modules,
        catalogItemCount: content.length,
      });
      const hasCredential = containsChatSecret(rawPrompt);
      const safeName = publicMaxBriefText(name);
      const safeIdea = publicMaxBriefText(idea);
      const safeAudience = publicMaxBriefText(audience);
      const safePrimaryAction = publicMaxBriefText(primaryAction);
      const safeBrandColors = publicMaxBriefText(brandColors);
      let prompt = buildMaxProjectPrompt({
        name: safeName,
        idea: safeIdea,
        appType,
        appTypes,
        audience: safeAudience,
        primaryAction: safePrimaryAction,
        features,
        style,
        brandColors: safeBrandColors,
        managementModules: modules,
        catalogItemCount: content.length,
      });
      const project = await createProject({
        name: safeName, template: "max_miniapp",
        staff_settings: { leads_enabled: true, orders_mode: hasOrders ? orderSettings.mode : null, prep_minutes: prepMinutes },
        max_config: {
          app_name: safeName,
          app_type: appType,
          summary: MAX_DEFAULT_SUMMARY,
          audience: safeAudience,
          primary_action: safePrimaryAction,
          features: serializeMaxProjectFeatures({ appType, appTypes, features }),
          style,
          brand_colors: safeBrandColors,
          content,
          operator: { legal_name: "" },
          support: { email: null, response_time: "Ответим в течение 2 рабочих дней" },
          legal: {
            age_rating: "0+",
            has_sales: appTypes.includes("catalog"),
            has_user_content: false,
            marketing_notifications: features.includes("Уведомления бота"),
            personal_data_consent: true,
            terms_accepted: false,
            policy_url: "",
          },
          max_url_attached: false,
          management_modules: modules,
        },
      });
      let credentialReady = !hasCredential;
      if (hasCredential) {
        try {
          const catalog = await getIntegrationCatalog(project.id);
          const resolution = resolveChatCredential(
            rawPrompt,
            catalog.providers,
            rawPrompt,
          );
          if (resolution.kind === "match") {
            const { provider, secretField, secret, safePrompt } = resolution.value;
            await connectAppIntegration(project.id, provider.key, {
              [secretField.key]: secret,
            });
            prompt = safePrompt;
            credentialReady = true;
          }
        } catch {
          credentialReady = false;
        }
      }
      return { project, prompt, credentialReady };
    },
    onSuccess: ({ project, prompt, credentialReady }) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      if (!credentialReady) {
        toast.warning("Проект создан, но ключ не подключён", {
          description:
            "Откройте проект и вставьте в чат точное название провайдера вместе с ключом ещё раз.",
        });
        router.push(`/max/${project.id}`);
        return;
      }
      toast.success("MAX Mini App создан", { description: "Открываем студию для первой сборки." });
      try {
        window.sessionStorage.setItem(`omnia:max:starter:${project.id}`, prompt);
        router.push(`/max/${project.id}?starter=1`);
      } catch {
        router.push(`/max/${project.id}?p=${encodeURIComponent(prompt)}`);
      }
    },
    onError: (error: unknown) => {
      toast.error("Не удалось создать приложение", {
        description: describeApiError(error, "Попробуйте ещё раз."),
      });
    },
  });

  function openWizard() {
    setDialogOpen(true);
  }

  /** Готовая идея заполняет мастер, а не создаёт проект молча. */
  function applyIdea(idea: MaxStudioIdea) {
    setName(idea.title);
    setIdea(idea.idea);
    setAppTypes([idea.appType]);
    setManagementModules(defaultManagementModules(idea.appType));
    setManagementModuleOptOuts([]);
    setCatalogDraft(null);
    setAudience(idea.audience);
    setPrimaryAction(idea.primaryAction);
    openWizard();
  }

  function updateBrief(patch: Partial<MaxProjectBrief>) {
    if (patch.name !== undefined) setName(patch.name);
    if (patch.idea !== undefined) setIdea(patch.idea);
    const changingTypes = patch.appTypes !== undefined || patch.appType !== undefined;
    const nextTypes = patch.appTypes !== undefined
      ? selectedMaxAppTypes(appType, patch.appTypes)
      : patch.appType !== undefined ? [patch.appType] : appTypes;
    if (changingTypes) setAppTypes(nextTypes);
    if (patch.audience !== undefined) setAudience(patch.audience);
    if (patch.primaryAction !== undefined) setPrimaryAction(patch.primaryAction);
    if (patch.features !== undefined) setFeatures(patch.features);
    if (patch.style !== undefined) setStyle(patch.style);
    if (patch.brandColors !== undefined) setBrandColors(patch.brandColors);
    if (patch.managementModules !== undefined) {
      const modules = newProjectManagementModules(nextTypes[0], patch.managementModules, nextTypes);
      if (changingTypes) {
        setManagementModules(modules.filter(module => !managementModuleOptOuts.includes(module)));
      } else {
        // Type defaults must not re-enable a module the owner explicitly disabled.
        setManagementModuleOptOuts(previous => [...new Set([
          ...previous.filter(module => !modules.includes(module)),
          ...managementModules.filter(module => !modules.includes(module)),
        ])]);
        setManagementModules(modules);
      }
    }
  }

  return (
    <div data-max-studio className="max-studio-workspace">
      <MaxStudioHeader email={email} showLearning={false} />
      <main className="max-projects-main">
        <div className="max-projects-content">
          <div className="max-projects-heading">
            <div>
              <p className="omnia-kicker text-accent">Ваша студия</p>
              <h1>Мои приложения</h1>
              <p>Продолжите работу или запустите новую идею в MAX.</p>
            </div>
            <Button data-learning="create-project" size="lg" disabled={quota.initialExhausted} onClick={openWizard}>
              <Plus className="size-4" /> Создать приложение
            </Button>
          </div>

          <OwnerChatQuotaNotice {...quota} initialOnly />
          <div className="max-projects-toolbar">
            <label data-learning="projects-search" className="max-projects-search">
              <Search className="size-4 shrink-0" aria-hidden="true" />
              <Input ref={searchField} type="search" aria-label="Найти проект" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Найти проект" />
            </label>
            {projects.isSuccess && <span className="text-sm text-fg-secondary" aria-live="polite">
              {search.trim() ? `Найдено: ${maxProjects.length} из ${allMaxProjects.length}` : `Всего: ${allMaxProjects.length}`}
            </span>}
          </div>

          {projects.isPending ? (
            <div role="status" aria-label="Загрузка приложений" className="space-y-3">
              <span className="sr-only">Загружаем приложения</span>
              {Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-28 rounded-lg" />)}
            </div>
          ) : projects.isError ? (
            <section role="alert" className="max-projects-empty">
              <CircleAlert className="mx-auto size-7 text-danger-fg" />
              <h2>{MAX_STATUS_COPY.projects.title}</h2>
              <p>{MAX_STATUS_COPY.projects.hint}</p>
              <Button variant="outline" disabled={projects.isFetching} onClick={() => void projects.refetch()}>{MAX_STATUS_COPY.projects.retry}</Button>
            </section>
          ) : search.trim() && maxProjects.length === 0 ? (
            <section className="max-projects-empty">
              <h2>Ничего не найдено</h2>
              <p>Попробуйте другое название или сбросьте поиск.</p>
              <Button variant="outline" onClick={() => setSearch("")}>Сбросить поиск</Button>
            </section>
          ) : allMaxProjects.length === 0 ? (
            <section className="max-projects-empty">
              <FolderKanban className="mx-auto size-7 text-accent" />
              <h2>Первого приложения ещё нет</h2>
              <p>Опишите задачу своими словами — ИИ соберёт рабочее приложение внутри MAX. Или возьмите готовую идею ниже.</p>
              <Button disabled={quota.initialExhausted} onClick={openWizard}>Описать свою идею</Button>
            </section>
          ) : (
            <section className="max-projects-list" aria-label="Приложения">
              <div className="max-projects-list-heading" aria-hidden="true">
                <span>Приложение</span><span>Состояние</span><span>Следующее действие</span>
              </div>
              {maxProjects.map((project) => <MaxStudioProjectCard key={project.id} project={project} fallbackFocus={searchField} />)}
            </section>
          )}

          {/* Две трети первого экрана оставались пустыми: ни подсказки, ни
              примеров. Идеи показываем и новичку, и владельцу пары проектов. */}
          {!projects.isPending && !projects.isError && !search.trim() && (
            <MaxStudioIdeas onPick={applyIdea} />
          )}
        </div>
      </main>
      <MaxProjectWizard
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        pending={create.isPending}
        submitDisabled={quota.initialExhausted || Boolean(catalogError)}
        quotaNotice={<OwnerChatQuotaNotice {...quota} initialOnly />}
        initialCatalog={<MaxInitialCatalog
          items={initialContent}
          pending={create.isPending}
          tooManyItems={tooManyItems}
          descriptionChanged={catalogDraft !== null && catalogDraft.source !== safeCatalogIdea}
          error={catalogError}
          onChange={(items) => setCatalogDraft({ source: safeCatalogIdea, items, tooManyItems: tooManyItems && items.length > 0 })}
          onReset={() => setCatalogDraft(null)}
        />}
        values={{ name, idea, appType, appTypes, audience, primaryAction, features, style, brandColors, managementModules }}
        orderSettings={orderSettings}
        onOrderSettingsChange={setOrderSettings}
        onChange={updateBrief}
        onSubmit={() => create.mutateAsync()}
      />
    </div>
  );
}
