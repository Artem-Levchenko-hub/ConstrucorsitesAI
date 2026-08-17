"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Check,
  ChevronRight,
  CircleHelp,
  Clock3,
  FolderKanban,
  LayoutGrid,
  Loader2,
  LogOut,
  Plus,
  Search,
  Settings,
  Sparkles,
  UserRound,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { logoutAction } from "@/app/(auth)/actions";
import { BrandMark } from "@/components/marketing/BrandMark";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  connectAppIntegration,
  getIntegrationCatalog,
} from "@/lib/api/app-integrations";
import { createProject, listProjects } from "@/lib/api/projects";
import { saveMaxProjectConfig } from "@/lib/api/max-studio";
import {
  MAX_APP_TYPES,
  MAX_FEATURES,
  MAX_STYLES,
  buildMaxProjectPrompt,
  type MaxAppTypeId,
  type MaxFeature,
  type MaxStyleId,
} from "@/lib/max-brief";
import {
  containsChatSecret,
  redactChatSecrets,
  resolveChatCredential,
} from "@/lib/max-chat-credentials";
import { cn } from "@/lib/utils";
import { MaxStudioProjectCard } from "./MaxStudioProjectCard";

const STARTER_FEATURES: MaxFeature[] = ["Профиль пользователя", "История действий"];

function StudioNav({ email }: { email: string }) {
  return (
    <aside className="hidden w-[220px] shrink-0 flex-col border-r border-[#d8d4cb] bg-[#fcfbf7] md:flex">
      <div className="flex h-16 items-center border-b border-[#d8d4cb] px-5">
        <BrandMark />
      </div>
      <nav className="flex-1 p-3 text-sm">
        <Link href="/max" className="flex items-center gap-3 rounded-[8px] bg-[#ece8df] px-3 py-2.5 font-medium">
          <LayoutGrid className="size-4" /> Проекты
        </Link>
        <Link href="/account" className="mt-1 flex items-center gap-3 rounded-[8px] px-3 py-2.5 text-[#6d6962] hover:bg-[#f5f3ee]">
          <UserRound className="size-4" /> Аккаунт
        </Link>
        <Link href="/account?tab=billing" className="mt-1 flex items-center gap-3 rounded-[8px] px-3 py-2.5 text-[#6d6962] hover:bg-[#f5f3ee]">
          <Clock3 className="size-4" /> Биллинг
        </Link>
      </nav>
      <div className="border-t border-[#d8d4cb] p-3">
        <p className="truncate px-3 text-xs font-medium">{email}</p>
        <p className="mt-1 px-3 text-[10px] text-[#8d887f]">Владелец MAX Studio</p>
        <form action={logoutAction}>
          <button className="mt-3 flex w-full items-center gap-3 rounded-[8px] px-3 py-2 text-xs text-[#6d6962] hover:bg-[#f5f3ee]">
            <LogOut className="size-3.5" /> Выйти
          </button>
        </form>
      </div>
    </aside>
  );
}

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

  const ready = name.trim().length > 1 && idea.trim().length > 9;
  const toggleFeature = (feature: MaxFeature) =>
    setFeatures((current) =>
      current.includes(feature)
        ? current.filter((item) => item !== feature)
        : [...current, feature],
    );

  return (
    <div data-light-shell className="flex h-full min-h-0 bg-[#f5f3ee] text-[#171716]">
      <StudioNav email={email} />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-[#d8d4cb] bg-[#fcfbf7] px-5 sm:px-7">
          <div className="flex items-center gap-3 md:hidden">
            <BrandMark />
          </div>
          <div className="hidden md:block">
            <p className="omnia-kicker text-[#8d887f]">MAX Studio</p>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/max/start" className="hidden rounded-[8px] px-3 py-2 text-xs text-[#6d6962] hover:bg-[#f5f3ee] sm:inline-flex">
              <CircleHelp className="mr-2 size-3.5" /> Быстрый старт
            </Link>
            <Button onClick={() => setDialogOpen(true)} className="bg-[#f15a38] text-white hover:bg-[#d94929]">
              <Plus className="size-4" /> Новый проект
            </Button>
          </div>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto px-5 py-8 sm:px-8 sm:py-10 lg:px-12">
          <div className="mx-auto max-w-[1120px]">
            <div className="flex flex-col gap-6 border-b border-[#d8d4cb] pb-8 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <p className="omnia-kicker text-[#f15a38]">Рабочее пространство</p>
                <h1 className="mt-3 text-[36px] font-semibold tracking-[-.045em] sm:text-[44px]">Мои приложения</h1>
                <p className="mt-2 text-sm text-[#6d6962]">Создание, публикация и управление MAX Mini Apps.</p>
              </div>
              {maxProjects.length > 0 && (
                <label className="relative w-full sm:w-[260px]">
                  <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[#aaa59b]" />
                  <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Найти проект" className="h-10 border-[#d8d4cb] bg-[#fcfbf7] pl-9" />
                </label>
              )}
            </div>

            {projects.isLoading ? (
              <div className="mt-8 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-[260px] rounded-[12px]" />)}
              </div>
            ) : maxProjects.length === 0 ? (
              <section className="mt-8 grid min-h-[460px] place-items-center rounded-[12px] border border-dashed border-[#c9c4b9] bg-[#fcfbf7] p-8 text-center">
                <div className="max-w-[430px]">
                  <span className="mx-auto grid size-12 place-items-center rounded-[10px] bg-[#ece8df] text-[#f15a38]"><FolderKanban className="size-5" /></span>
                  <h2 className="mt-6 text-2xl font-semibold tracking-[-.025em]">Первого проекта ещё нет</h2>
                  <p className="mt-3 text-sm leading-6 text-[#6d6962]">Опишите задачу — Omnia создаст рабочее приложение, а затем проведёт через интеграции, MAX-бота и публикацию.</p>
                  <Button onClick={() => setDialogOpen(true)} className="mt-7 bg-[#f15a38] text-white hover:bg-[#d94929]">
                    <Plus className="size-4" /> Создать приложение
                  </Button>
                </div>
              </section>
            ) : (
              <div className="mt-8 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {maxProjects.map((project, index) => (
                  <MaxStudioProjectCard
                    key={project.id}
                    project={project}
                    index={index}
                  />
                ))}
                <button onClick={() => setDialogOpen(true)} className="grid min-h-[280px] place-items-center rounded-[12px] border border-dashed border-[#c9c4b9] bg-transparent p-8 text-center hover:bg-[#fcfbf7]">
                  <span><span className="mx-auto grid size-11 place-items-center rounded-[8px] border border-[#d8d4cb] bg-[#fcfbf7]"><Plus className="size-5 text-[#f15a38]" /></span><span className="mt-4 block text-sm font-semibold">Новый проект</span></span>
                </button>
              </div>
            )}
          </div>
        </main>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent
          data-light-shell
          className="flex max-h-[calc(100dvh-1rem)] flex-col gap-0 overflow-hidden border-[#d8d4cb] bg-[#fcfbf7] p-0 text-[#171716] sm:max-h-[92dvh] sm:max-w-[720px] sm:p-0"
        >
          <form
            className="contents"
            onSubmit={(event) => {
              event.preventDefault();
              if (ready && !create.isPending) create.mutate();
            }}
          >
            <div className="shrink-0 border-b border-[#d8d4cb] px-5 pb-5 pr-16 pt-5 sm:p-6 sm:pr-14">
              <div>
                <p className="omnia-kicker text-[#f15a38]">Новый MAX-проект</p>
                <DialogTitle className="mt-2 text-2xl font-semibold text-[#171716]">
                  Что создаём?
                </DialogTitle>
                <DialogDescription className="mt-1 text-sm text-[#6d6962]">
                  Короткого описания достаточно для первой сборки.
                </DialogDescription>
              </div>
            </div>

            <div className="min-h-0 flex-1 space-y-6 overflow-y-auto overscroll-contain p-5 sm:p-6">
              <div className="space-y-2">
                <Label htmlFor="max-project-name">Название</Label>
                <Input id="max-project-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Например, Кофе рядом" className="h-11 border-[#d8d4cb] bg-white" maxLength={100} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="max-project-idea">Что пользователь сможет делать?</Label>
                <Textarea id="max-project-idea" value={idea} onChange={(event) => setIdea(event.target.value)} placeholder="Получать баллы, выбирать награды и оформлять заказ к выдаче" className="min-h-24 resize-none border-[#d8d4cb] bg-white" maxLength={600} />
              </div>
              <fieldset>
                <legend className="text-sm font-medium">Тип приложения</legend>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {MAX_APP_TYPES.map((item) => (
                    <button key={item.id} type="button" onClick={() => setAppType(item.id)} className={cn("rounded-[10px] border p-3 text-left", appType === item.id ? "border-[#f15a38] bg-[#f15a38]/[.06]" : "border-[#d8d4cb] hover:border-[#aaa59b]")}>
                      <span className="flex items-center justify-between text-sm font-medium">{item.label}{appType === item.id && <Check className="size-4 text-[#f15a38]" />}</span>
                      <span className="mt-1 block text-xs leading-5 text-[#8d887f]">{item.description}</span>
                    </button>
                  ))}
                </div>
              </fieldset>

              <details className="group rounded-[10px] border border-[#d8d4cb]">
                <summary className="flex cursor-pointer list-none items-center justify-between p-4 text-sm font-medium [&::-webkit-details-marker]:hidden">
                  Уточнить функции и стиль <ChevronRight className="size-4 text-[#8d887f] transition-transform group-open:rotate-90" />
                </summary>
                <div className="space-y-5 border-t border-[#e7e3da] p-4">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-2"><Label htmlFor="max-audience">Аудитория</Label><Input id="max-audience" value={audience} onChange={(event) => setAudience(event.target.value)} className="border-[#d8d4cb] bg-white" /></div>
                    <div className="space-y-2"><Label htmlFor="max-action">Главное действие</Label><Input id="max-action" value={primaryAction} onChange={(event) => setPrimaryAction(event.target.value)} className="border-[#d8d4cb] bg-white" /></div>
                  </div>
                  <div>
                    <p className="text-sm font-medium">Функции</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {MAX_FEATURES.map((feature) => (
                        <button key={feature} type="button" onClick={() => toggleFeature(feature)} className={cn("rounded-full border px-3 py-1.5 text-xs", features.includes(feature) ? "border-[#f15a38] bg-[#f15a38]/[.07] text-[#c84528]" : "border-[#d8d4cb] text-[#6d6962]")}>{feature}</button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <p className="text-sm font-medium">Стиль</p>
                    <div className="mt-2 grid gap-2 sm:grid-cols-3">
                      {MAX_STYLES.map((item) => (
                        <button key={item.id} type="button" onClick={() => setStyle(item.id)} className={cn("rounded-[8px] border p-3 text-left text-xs", style === item.id ? "border-[#f15a38] bg-[#f15a38]/[.07]" : "border-[#d8d4cb]")}>{item.label}</button>
                      ))}
                    </div>
                  </div>
                  <div className="space-y-2"><Label htmlFor="max-brand">Цвета бренда</Label><Input id="max-brand" value={brandColors} onChange={(event) => setBrandColors(event.target.value)} placeholder="#F15A38, графит, молочный" className="border-[#d8d4cb] bg-white" /></div>
                </div>
              </details>
            </div>

            <div className="flex shrink-0 flex-col-reverse items-stretch gap-3 border-t border-[#d8d4cb] bg-[#fcfbf7] p-4 pb-[max(1rem,env(safe-area-inset-bottom))] sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:p-5">
              <p className="hidden text-xs text-[#8d887f] sm:block">Генерация начнётся один раз после открытия проекта.</p>
              <div className="flex flex-col-reverse gap-2 sm:ml-auto sm:flex-row">
                <Button type="button" variant="outline" className="min-h-11" onClick={() => setDialogOpen(false)}>Отмена</Button>
                <Button disabled={!ready || create.isPending} className="min-h-11 bg-[#f15a38] text-white hover:bg-[#d94929]">
                  {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
                  Создать проект
                </Button>
              </div>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
