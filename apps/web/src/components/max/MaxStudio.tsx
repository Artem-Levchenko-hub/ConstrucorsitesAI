"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, FolderKanban, Plus, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { connectAppIntegration, getIntegrationCatalog } from "@/lib/api/app-integrations";
import { createProject, listProjects } from "@/lib/api/projects";
import { saveMaxProjectConfig } from "@/lib/api/max-studio";
import { buildMaxProjectPrompt, type MaxAppTypeId, type MaxFeature, type MaxStyleId } from "@/lib/max-brief";
import { containsChatSecret, redactChatSecrets, resolveChatCredential } from "@/lib/max-chat-credentials";
import { MaxStudioProjectCard } from "./MaxStudioProjectCard";
import { MaxStudioHeader } from "./MaxStudioHeader";
import { MaxProjectWizard } from "./MaxProjectWizard";
import "./max-studio.css";

const STARTER_FEATURES: MaxFeature[] = ["Профиль пользователя", "История действий"];

export function MaxStudio({ email }: { email: string }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [name, setName] = useState("");
  const [idea, setIdea] = useState("");
  const [appType, setAppType] = useState<MaxAppTypeId>("loyalty");
  const [audience, setAudience] = useState("");
  const [primaryAction, setPrimaryAction] = useState("");
  const [features, setFeatures] = useState<MaxFeature[]>(STARTER_FEATURES);
  const [style, setStyle] = useState<MaxStyleId>("brand");
  const [brandColors, setBrandColors] = useState("");

  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const maxProjects = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("ru-RU");
    return (projects.data ?? []).filter(
      (project) =>
        project.template === "max_miniapp" &&
        (!needle || project.name.toLocaleLowerCase("ru-RU").includes(needle)),
    );
  }, [projects.data, search]);

  const create = useMutation({
    mutationFn: async () => {
      const rawPrompt = buildMaxProjectPrompt({
        name,
        idea,
        appType,
        audience,
        primaryAction,
        features,
        style,
        brandColors,
      });
      const hasCredential = containsChatSecret(rawPrompt);
      const scrub = (value: string) =>
        redactChatSecrets(value).replaceAll(
          "[ключ сохранён в Omnia]",
          "[секрет удалён из описания]",
        );
      const safeName = hasCredential ? scrub(name).trim() : name.trim();
      const safeIdea = hasCredential ? scrub(idea).trim() : idea.trim();
      const safeAudience = hasCredential ? scrub(audience).trim() : audience.trim();
      const safePrimaryAction = hasCredential
        ? scrub(primaryAction).trim()
        : primaryAction.trim();
      const safeBrandColors = hasCredential
        ? scrub(brandColors).trim()
        : brandColors.trim();
      const project = await createProject({
        name: safeName,
        template: "max_miniapp",
      });
      let prompt = buildMaxProjectPrompt({
        name: safeName,
        idea: safeIdea,
        appType,
        audience: safeAudience,
        primaryAction: safePrimaryAction,
        features,
        style,
        brandColors: safeBrandColors,
      });
      let configSaved = true;
      try {
        await saveMaxProjectConfig(project.id, {
          app_name: safeName,
          app_type: appType,
          summary: safeIdea,
          audience: safeAudience,
          primary_action: safePrimaryAction,
          features,
          style,
          brand_colors: safeBrandColors,
          content: [],
          operator: { legal_name: "", inn: "", ogrn: "", address: "" },
          support: { email: null, phone: "", response_time: "Ответим в течение 2 рабочих дней" },
          legal: {
            age_rating: "0+",
            has_sales: appType === "catalog",
            has_user_content: false,
            marketing_notifications: features.includes("Уведомления бота"),
            personal_data_consent: true,
            terms_accepted: false,
          },
          max_url_attached: false,
        });
      } catch {
        configSaved = false;
      }
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
      return { project, prompt, configSaved, credentialReady };
    },
    onSuccess: ({ project, prompt, configSaved, credentialReady }) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      if (!credentialReady) {
        toast.warning("Проект создан, но ключ не подключён", {
          description:
            "Откройте проект и вставьте в чат точное название провайдера вместе с ключом ещё раз.",
        });
        router.push(`/max/${project.id}`);
        return;
      }
      toast[configSaved ? "success" : "warning"](
        configSaved ? "MAX Mini App создан" : "Приложение создано",
        {
          description: configSaved
            ? "Открываем студию для первой сборки."
            : "Профиль нужно сохранить в панели готовности.",
        },
      );
      try {
        window.sessionStorage.setItem(`omnia:max:starter:${project.id}`, prompt);
        router.push(`/max/${project.id}?starter=1`);
      } catch {
        router.push(`/max/${project.id}?p=${encodeURIComponent(prompt)}`);
      }
    },
    onError: (error: unknown) => {
      toast.error("Не удалось создать приложение", {
        description: error instanceof Error ? error.message : "Попробуйте ещё раз.",
      });
    },
  });

  const allMaxProjects = (projects.data ?? []).filter((project) => project.template === "max_miniapp");

  return (
    <div data-max-studio className="max-studio-workspace">
      <MaxStudioHeader email={email} />
      <main className="max-projects-main">
        <div className="max-projects-content">
          <div className="max-projects-heading">
            <div>
              <p className="omnia-kicker text-accent">Ваша студия</p>
              <h1>Мои приложения</h1>
              <p>Продолжите работу или запустите новую идею в MAX.</p>
            </div>
            <Button size="lg" onClick={() => setDialogOpen(true)}>
              <Plus className="size-4" /> Создать приложение
            </Button>
          </div>

          <div className="max-projects-toolbar">
            <label className="max-projects-search">
              <Search className="size-4 shrink-0" aria-hidden="true" />
              <Input type="search" aria-label="Найти проект" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Найти проект" />
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
              <h2>Не удалось загрузить приложения</h2>
              <p>Проверьте подключение и попробуйте ещё раз.</p>
              <Button variant="outline" disabled={projects.isFetching} onClick={() => void projects.refetch()}>Повторить</Button>
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
              <h2>Первого проекта ещё нет</h2>
              <p>Опишите задачу — MAX Studio поможет создать приложение и подготовить его к запуску.</p>
              <Button variant="outline" onClick={() => setDialogOpen(true)}>Описать идею</Button>
            </section>
          ) : (
            <section className="max-projects-list" aria-label="Приложения">
              <div className="max-projects-list-heading" aria-hidden="true">
                <span>Приложение</span><span>Состояние</span><span>Следующее действие</span>
              </div>
              {maxProjects.map((project) => <MaxStudioProjectCard key={project.id} project={project} />)}
            </section>
          )}
        </div>
      </main>
      <MaxProjectWizard
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        pending={create.isPending}
        values={{ name, idea, appType, audience, primaryAction, features, style, brandColors }}
        onChange={(patch) => {
          if (patch.name !== undefined) setName(patch.name);
          if (patch.idea !== undefined) setIdea(patch.idea);
          if (patch.appType !== undefined) setAppType(patch.appType);
          if (patch.audience !== undefined) setAudience(patch.audience);
          if (patch.primaryAction !== undefined) setPrimaryAction(patch.primaryAction);
          if (patch.features !== undefined) setFeatures(patch.features);
          if (patch.style !== undefined) setStyle(patch.style);
          if (patch.brandColors !== undefined) setBrandColors(patch.brandColors);
        }}
        onSubmit={() => create.mutateAsync()}
      />
    </div>
  );
}
