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
import { peekLandingPrompt, takeLandingPrompt } from "@/lib/landing-prompt";
import { createProject, listProjects } from "@/lib/api/projects";
import { MAX_DEFAULT_SUMMARY, availableManagementModules, briefManagementModuleOptOuts, briefSupportEmail, buildMaxProjectPrompt, newProjectManagementModules, selectedMaxAppTypes, serializeMaxProjectFeatures, type MaxProjectBrief } from "@/lib/max-brief";
import { completeMaxProjectDraft, dismissEmptyMaxProjectDraft, emptyMaxProjectDraft, hasMaxProjectDraftAnswers, readMaxProjectDraft, replaceMaxProjectDraft, saveMaxProjectDraft, startMaxProjectDraft, type MaxProjectDraft } from "@/lib/max-project-draft";
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
import { MaxProjectWizard } from "./MaxProjectWizard";
import { MaxInitialCatalog } from "./MaxInitialCatalog";
import "./max-studio.css";

export function MaxStudio({ email }: { email: string }) {
  // A changed authenticated owner gets a distinct in-memory and durable draft.
  return <OwnedMaxStudio key={email.trim().toLowerCase()} email={email} />;
}

function OwnedMaxStudio({ email }: { email: string }) {
  const router = useRouter();
  const quota = useOwnerChatQuota();
  const qc = useQueryClient();
  const learning = useLearningGate();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [search, setSearch] = useState("");
  const searchField = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState<MaxProjectDraft>(emptyMaxProjectDraft);
  const draftRef = useRef(draft);
  const completedDraft = useRef(false);
  const mounted = useRef(false);
  const [draftReady, setDraftReady] = useState(false);
  const [draftStorageFailed, setDraftStorageFailed] = useState(false);
  const { name, idea, appTypes, audience, primaryAction, features, brandColors, managementModules,
    managementModuleOptOuts, catalogDraft } = draft;
  const appType = appTypes[0];
  const safeIdea = useMemo(() => publicMaxBriefText(idea), [idea]);
  const supportEmail = draft.supportEmail ?? briefSupportEmail(safeIdea) ?? "";
  const moduleOptOuts = [...new Set([...managementModuleOptOuts, ...briefManagementModuleOptOuts(safeIdea)])];
  const modules = newProjectManagementModules(appType, managementModules, appTypes, moduleOptOuts);
  const availableModules = availableManagementModules(moduleOptOuts);
  const proposedCatalog = useMemo(() => {
    const result = extractInitialCatalog(safeIdea);
    return { ...result, items: result.items.map(item => ({ ...newMaxContentItem(), ...item })) };
  }, [safeIdea]);
  const initialContent = catalogDraft?.items ?? proposedCatalog.items;
  const tooManyItems = catalogDraft?.tooManyItems ?? proposedCatalog.tooManyItems;
  // Reviewing supplied data does not select a mandatory product catalog screen.
  // An edited empty draft is an explicit decision and must survive reload.
  const reviewCatalog = availableModules.includes("catalog") && (
    modules.includes("catalog") || initialContent.length > 0 || catalogDraft !== null || tooManyItems
  );
  const catalogError = reviewCatalog ? initialCatalogError(publicInitialCatalog(initialContent), tooManyItems) : null;

  const projects = useQuery({ queryKey: learning ? ["projects", learning.userKey] : ["projects"], queryFn: listProjects });
  const allMaxProjects = useMemo(() => (projects.data ?? []).filter(project => project.template === "max_miniapp"), [projects.data]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  function updateDraft(patch: Partial<MaxProjectDraft>) {
    if (completedDraft.current) return;
    const next = { ...draftRef.current, ...patch };
    draftRef.current = next;
    setDraft(next);
    setDraftStorageFailed(!saveMaxProjectDraft(email, next));
  }

  // Persist the destination before consuming the registration handoff. Strict
  // mode cleanup only cancels the frame; it never consumes or rewrites the draft.
  useEffect(() => {
    const saved = readMaxProjectDraft(email);
    const pending = saved ? null : peekLandingPrompt();
    const next = saved ?? { ...draftRef.current, ...(pending ? { idea: pending } : {}) };
    const frame = window.requestAnimationFrame(() => {
      draftRef.current = next;
      setDraft(next);
      if (pending) {
        const persisted = startMaxProjectDraft(email, next);
        setDraftStorageFailed(!persisted);
        if (persisted && peekLandingPrompt() === pending) takeLandingPrompt();
      }
      setDialogOpen(Boolean(saved || pending));
      setDraftReady(true);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [email]);
  const maxProjects = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("ru-RU");
    return allMaxProjects.filter(project => !needle || project.name.toLocaleLowerCase("ru-RU").includes(needle));
  }, [allMaxProjects, search]);

  const create = useMutation({
    mutationFn: async () => {
      const submittedDraftId = draft.id;
      const submittedOwner = email;
      if (quota.initialExhausted) throw new Error(FREE_CHAT_UPGRADE_MESSAGE);
      const content = reviewCatalog ? publicInitialCatalog(initialContent) : [];
      if (catalogError) throw new Error(catalogError);
      const brief = {
        name, idea, appType, appTypes, audience, primaryAction, features, brandColors,
        supportEmail: draft.supportEmail ?? (supportEmail || undefined),
        managementModules: modules,
        managementModuleOptOuts: moduleOptOuts,
        catalogItemCount: reviewCatalog ? content.length : undefined,
      };
      const rawPrompt = buildMaxProjectPrompt(brief);
      const hasCredential = containsChatSecret(rawPrompt);
      const safeName = publicMaxBriefText(name);
      const safeAudience = publicMaxBriefText(audience);
      const safePrimaryAction = publicMaxBriefText(primaryAction);
      const safeBrandColors = publicMaxBriefText(brandColors);
      let prompt = buildMaxProjectPrompt({
        ...brief,
        name: safeName,
        idea: safeIdea,
        audience: safeAudience,
        primaryAction: safePrimaryAction,
        brandColors: safeBrandColors,
      });
      const project = await createProject({
        name: safeName, template: "max_miniapp",
        // Unspecified UI choices do not deny SDK capabilities. Only explicit
        // exclusions constrain availability; these do not activate integrations.
        management_module_opt_outs: moduleOptOuts,
        // The owner can start the order queue and choose its payment mode in Team.
        staff_settings: { leads_enabled: modules.includes("leads"), orders_mode: null, prep_minutes: 7 },
        max_config: {
          app_name: safeName,
          app_type: appType,
          summary: MAX_DEFAULT_SUMMARY,
          audience: safeAudience,
          primary_action: safePrimaryAction,
          features: serializeMaxProjectFeatures({ appType, appTypes, features }),
          // Retain the deployed config contract without imposing a visual preset.
          style: "brand",
          brand_colors: safeBrandColors,
          content,
          operator: { legal_name: "" },
          support: { email: supportEmail.trim() || null, response_time: "Ответим в течение 2 рабочих дней" },
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
          management_modules: availableModules,
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
      return { project, prompt, credentialReady, submittedDraftId, submittedOwner };
    },
    onSuccess: ({ project, prompt, credentialReady, submittedDraftId, submittedOwner }) => {
      completeMaxProjectDraft(submittedOwner, submittedDraftId);
      // An old owner's accepted creation remains theirs after navigation. Do
      // not route or mutate the visible state of a remounted/different owner.
      if (!mounted.current || submittedOwner !== email) return;
      completedDraft.current = true;
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
      if (!mounted.current) return;
      toast.error("Не удалось создать приложение", {
        description: describeApiError(error, "Попробуйте ещё раз."),
      });
    },
  });

  function openWizard() {
    if (!draftReady || completedDraft.current) return;
    setDraftStorageFailed(!startMaxProjectDraft(email, draftRef.current));
    setDialogOpen(true);
  }

  function changeWizardOpen(open: boolean) {
    if (!open) dismissEmptyMaxProjectDraft(email, draftRef.current);
    setDialogOpen(open);
  }

  /** Готовая идея заполняет мастер, а не создаёт проект молча. */
  function applyIdea(idea: MaxStudioIdea) {
    if (!draftReady || completedDraft.current) return;
    const current = draftRef.current;
    const dirty = hasMaxProjectDraftAnswers(current);
    if (dirty && !window.confirm("Заменить текущий черновик выбранной идеей?")) return;
    const replacement = { ...emptyMaxProjectDraft(), name: idea.title, idea: idea.idea,
      appType: idea.appType, appTypes: [idea.appType], audience: idea.audience, primaryAction: idea.primaryAction };
    const persisted = replaceMaxProjectDraft(email, current.id, replacement)
      || (!dirty && startMaxProjectDraft(email, replacement));
    if (!persisted) {
      setDraftStorageFailed(true);
      toast.warning("Не удалось заменить черновик", {
        description: "Он изменился в другой вкладке или хранилище недоступно. Текущие ответы не изменены. Обновите страницу и проверьте сохранённую идею.",
      });
      return;
    }
    draftRef.current = replacement;
    setDraft(replacement);
    setDraftStorageFailed(false);
    setDialogOpen(true);
  }

  function updateBrief(patch: Partial<Omit<MaxProjectBrief, "catalogItemCount">>) {
    const { managementModuleOptOuts: excluded, ...fields } = patch;
    const changingTypes = patch.appTypes !== undefined || patch.appType !== undefined;
    const nextTypes = patch.appTypes !== undefined
      ? selectedMaxAppTypes(appType, patch.appTypes)
      : patch.appType !== undefined ? [patch.appType] : appTypes;
    updateDraft({ ...fields, ...(changingTypes ? { appTypes: nextTypes, appType: nextTypes[0] } : {}),
      managementModuleOptOuts: excluded === undefined ? draftRef.current.managementModuleOptOuts : [...excluded] });
  }

  return (
    <div data-max-studio className="max-studio-workspace">
      <MaxStudioHeader email={email} showLearning={false} />
      <main className="max-projects-main">
        <div className="max-projects-content">
          <div className="max-projects-heading">
            <div>
              <p className="omnia-kicker text-accent">Ваша студия</p>
              <h1><span className="max-projects-title-prefix">Мои </span>приложения</h1>
            </div>
            <Button data-learning="create-project" aria-label="Создать приложение" size="lg" disabled={!draftReady || quota.initialExhausted} onClick={openWizard}>
              <Plus aria-hidden="true" className="size-4" /><span>Создать<span className="max-projects-action-suffix"> приложение</span></span>
            </Button>
          </div>

          <OwnerChatQuotaNotice {...quota} initialOnly />
          <div className="max-projects-toolbar">
            <label data-learning="projects-search" className="max-projects-search">
              <Search className="size-4 shrink-0" aria-hidden="true" />
              <Input ref={searchField} type="search" aria-label="Найти проект" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Найти проект" />
            </label>
            {projects.isSuccess && <span className="max-projects-count text-sm text-fg-secondary" data-filtering={Boolean(search.trim())} aria-live="polite">
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
              <Button disabled={quota.initialExhausted} onClick={openWizard}>Создать первое приложение</Button>
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
        onOpenChange={changeWizardOpen}
        pending={create.isPending}
        submitDisabled={quota.initialExhausted || Boolean(catalogError)}
        quotaNotice={<><OwnerChatQuotaNotice {...quota} initialOnly />{draftStorageFailed && <p role="status" className="text-sm text-fg-secondary">Не удалось сохранить черновик в этом браузере. Не закрывайте страницу до создания проекта.</p>}</>}
        initialCatalog={reviewCatalog && <MaxInitialCatalog
          items={initialContent}
          pending={create.isPending}
          tooManyItems={tooManyItems}
          descriptionChanged={catalogDraft !== null && catalogDraft.source !== safeIdea}
          error={catalogError}
          onChange={(items) => updateDraft({ catalogDraft: { source: safeIdea, items, tooManyItems: tooManyItems && items.length > 0 } })}
          onReset={() => updateDraft({ catalogDraft: null })}
        />}
        values={{ name, idea, appType, appTypes, audience, primaryAction, features, brandColors, supportEmail, managementModules, managementModuleOptOuts }}
        onChange={updateBrief}
        onSubmit={() => create.mutateAsync()}
      />
    </div>
  );
}
