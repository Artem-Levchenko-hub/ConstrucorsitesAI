"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Bot,
  Check,
  ChevronRight,
  Loader2,
  MessageCircleMore,
  Rocket,
  ShieldCheck,
  Sparkles,
  WandSparkles,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { ProjectCard } from "@/components/projects/ProjectCard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  MAX_APP_TYPES,
  MAX_FEATURES,
  MAX_STYLES,
  buildMaxProjectPrompt,
  type MaxAppTypeId,
  type MaxFeature,
  type MaxStyleId,
} from "@/lib/max-brief";
import { createProject, listProjects } from "@/lib/api/projects";
import { saveMaxProjectConfig } from "@/lib/api/max-studio";
import { cn } from "@/lib/utils";
import { MaxStudioHeader } from "./MaxStudioHeader";

const STARTER_FEATURES: MaxFeature[] = [
  "Профиль пользователя",
  "История действий",
];

export function MaxStudio({ email }: { email: string }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [idea, setIdea] = useState("");
  const [appType, setAppType] = useState<MaxAppTypeId>("loyalty");
  const [audience, setAudience] = useState("");
  const [primaryAction, setPrimaryAction] = useState("");
  const [features, setFeatures] =
    useState<MaxFeature[]>(STARTER_FEATURES);
  const [style, setStyle] = useState<MaxStyleId>("brand");
  const [brandColors, setBrandColors] = useState("");

  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });
  const maxProjects = useMemo(
    () => (projects.data ?? []).filter((project) => project.template === "max_miniapp"),
    [projects.data],
  );

  const create = useMutation({
    mutationFn: async () => {
      const project = await createProject({
        name: name.trim(),
        template: "max_miniapp",
      });
      const prompt = buildMaxProjectPrompt({
        name,
        idea,
        appType,
        audience,
        primaryAction,
        features,
        style,
        brandColors,
      });
      let configSaved = true;
      try {
        await saveMaxProjectConfig(project.id, {
          app_name: name.trim(),
          app_type: appType,
          summary: idea.trim(),
          audience: audience.trim(),
          primary_action: primaryAction.trim(),
          features,
          style,
          brand_colors: brandColors.trim(),
          content: [],
          operator: { legal_name: "", inn: "", ogrn: "", address: "" },
          support: {
            email: null,
            phone: "",
            response_time: "Ответим в течение 2 рабочих дней",
          },
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
        // The project itself already exists. Continue into it instead of
        // inviting a retry that would create a duplicate; the same form is
        // available from the launch panel.
        configSaved = false;
      }
      return { project, prompt, configSaved };
    },
    onSuccess: ({ project, prompt, configSaved }) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      if (configSaved) {
        toast.success("MAX Mini App создан", {
          description: "Бизнес-профиль сохранён. Открываем студию для первой сборки.",
        });
      } else {
        toast.warning("Приложение создано", {
          description: "Профиль нужно сохранить в панели готовности.",
        });
      }
      try {
        window.sessionStorage.setItem(`omnia:max:starter:${project.id}`, prompt);
        router.push(`/max/${project.id}?starter=1`);
      } catch {
        // A browser can disable sessionStorage. Keep the flow usable with the
        // existing one-shot query handoff as a rare fallback.
        router.push(`/max/${project.id}?p=${encodeURIComponent(prompt)}`);
      }
    },
    onError: (error: unknown) => {
      toast.error("Не удалось создать приложение", {
        description:
          error instanceof Error ? error.message : "Попробуйте ещё раз.",
      });
    },
  });

  function toggleFeature(feature: MaxFeature) {
    setFeatures((current) =>
      current.includes(feature)
        ? current.filter((item) => item !== feature)
        : [...current, feature],
    );
  }

  const ready = name.trim().length > 1 && idea.trim().length > 9;

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-[#080a10] text-white">
      <MaxStudioHeader email={email} />

      <main className="max-studio-scroll flex-1 overflow-y-auto">
        <section
          id="top"
          className="border-b border-white/[0.12]"
        >
          <div className="mx-auto grid w-full max-w-[1440px] lg:grid-cols-[minmax(0,1.08fr)_minmax(420px,0.92fr)]">
            <div className="flex flex-col justify-center">
              <div className="px-5 py-12 sm:px-8 lg:px-12 lg:py-16 xl:px-16">
              <div className="mb-8 inline-flex w-fit items-center gap-2 border border-white/20 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-white/55">
                <Sparkles className="h-3.5 w-3.5 text-[#8b5cf6]" />
                Рабочая среда для MAX
              </div>
              <h1 className="max-w-3xl text-4xl font-semibold leading-[0.94] tracking-[-0.05em] sm:text-5xl xl:text-[70px]">
                Соберите приложение для MAX и доведите его до запуска
              </h1>
              <p className="mt-7 max-w-2xl text-base leading-7 text-white/50 sm:text-lg">
                Опишите бизнес-сценарий обычными словами. Студия соберёт
                мини-приложение, сохранит прогресс и проведёт через подключение
                бота, HTTPS-публикацию и webhook.
              </p>

              <div className="mt-10 hidden max-w-2xl border-y border-white/15 sm:grid sm:grid-cols-3">
                {[
                  {
                    Icon: WandSparkles,
                    title: "Опишите задачу",
                    text: "Короткий продуктовый бриф",
                  },
                  {
                    Icon: Rocket,
                    title: "Проверьте сборку",
                    text: "Мобильное превью и build",
                  },
                  {
                    Icon: Bot,
                    title: "Запустите в MAX",
                    text: "Бот, HTTPS и webhook",
                  },
                ].map(({ Icon, title, text }) => (
                  <div
                    key={title}
                    className="border-b border-white/15 py-5 sm:border-b-0 sm:border-r sm:px-5 first:sm:pl-0 last:sm:border-r-0"
                  >
                    <Icon className="mb-8 h-5 w-5 text-[#8b5cf6]" />
                    <div className="text-sm font-medium">{title}</div>
                    <div className="mt-1 text-xs leading-5 text-white/40">{text}</div>
                  </div>
                ))}
              </div>
              </div>
            </div>

            <form
              className="border-t border-white/[0.12] bg-[#0f121f] p-5 sm:p-8 lg:border-l lg:border-t-0 lg:p-10"
              onSubmit={(event) => {
                event.preventDefault();
                if (ready && !create.isPending) create.mutate();
              }}
            >
              <div className="mb-7 flex items-start justify-between gap-4">
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[#8b5cf6]">
                    Новый MAX-проект
                  </p>
                  <h2 className="mt-2 text-2xl font-semibold tracking-tight">
                    Что создаём?
                  </h2>
                </div>
                <div className="border border-white/[0.12] p-2.5 text-white/40">
                  <MessageCircleMore className="h-5 w-5" />
                </div>
              </div>

              <div className="space-y-6">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2 sm:col-span-2">
                    <Label htmlFor="max-project-name">Название</Label>
                    <Input
                      id="max-project-name"
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                      placeholder="Например, Кофе рядом"
                      className="h-11 rounded-md border-white/[0.14] bg-[#080a10]"
                      autoComplete="off"
                      maxLength={100}
                    />
                  </div>
                  <div className="space-y-2 sm:col-span-2">
                    <Label htmlFor="max-project-idea">
                      Что пользователь сможет делать?
                    </Label>
                    <Textarea
                      id="max-project-idea"
                      value={idea}
                      onChange={(event) => setIdea(event.target.value)}
                      placeholder="Получать баллы за покупки, выбирать награды и видеть персональные акции"
                      className="min-h-24 resize-none rounded-md border-white/[0.14] bg-[#080a10]"
                      maxLength={600}
                    />
                  </div>
                </div>

                <fieldset className="space-y-3">
                  <legend className="text-sm font-medium">Тип приложения</legend>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {MAX_APP_TYPES.map((item) => {
                      const active = appType === item.id;
                      return (
                        <button
                          key={item.id}
                          type="button"
                          aria-pressed={active}
                          onClick={() => setAppType(item.id)}
                          className={cn(
                            "rounded-md border p-3 text-left transition-colors",
                            active
                              ? "border-[#8b5cf6] bg-[#3b82f6]/14"
                              : "border-white/[0.1] bg-transparent hover:border-white/[0.22]",
                          )}
                        >
                          <span className="flex items-center justify-between gap-2">
                            <span className="text-sm font-medium">{item.label}</span>
                            {active && <Check className="h-4 w-4 text-[#8b5cf6]" />}
                          </span>
                          <span className="mt-1 block text-xs leading-4 text-white/40">
                            {item.description}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </fieldset>

                <details className="group border border-white/[0.1] bg-transparent">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3.5 text-sm font-medium [&::-webkit-details-marker]:hidden">
                    Уточнить функции и стиль
                    <ChevronRight className="h-4 w-4 text-white/35 transition-transform group-open:rotate-90" />
                  </summary>
                  <div className="space-y-5 border-t border-white/[0.07] p-4">
                    <div className="grid gap-4 sm:grid-cols-2">
                      <div className="space-y-2">
                        <Label htmlFor="max-audience">Для кого</Label>
                        <Input
                          id="max-audience"
                          value={audience}
                          onChange={(event) => setAudience(event.target.value)}
                          placeholder="Постоянные гости"
                          className="rounded-md border-white/[0.14] bg-[#080a10]"
                          maxLength={160}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="max-action">Главное действие</Label>
                        <Input
                          id="max-action"
                          value={primaryAction}
                          onChange={(event) => setPrimaryAction(event.target.value)}
                          placeholder="Обменять баллы"
                          className="rounded-md border-white/[0.14] bg-[#080a10]"
                          maxLength={160}
                        />
                      </div>
                    </div>

                    <div className="space-y-2.5">
                      <Label>Возможности</Label>
                      <div className="flex flex-wrap gap-2">
                        {MAX_FEATURES.map((feature) => {
                          const active = features.includes(feature);
                          return (
                            <button
                              key={feature}
                              type="button"
                              aria-pressed={active}
                              onClick={() => toggleFeature(feature)}
                              className={cn(
                                "rounded-md border px-3 py-1.5 text-xs transition-colors",
                                active
                                  ? "border-[#8b5cf6]/70 bg-[#3b82f6]/16 text-white"
                                  : "border-white/[0.1] text-white/50 hover:border-white/20 hover:text-white/75",
                              )}
                            >
                              {active && <Check className="mr-1 inline h-3 w-3" />}
                              {feature}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    <div className="space-y-2.5">
                      <Label>Визуальный стиль</Label>
                      <div className="grid gap-2 sm:grid-cols-3">
                        {MAX_STYLES.map((item) => (
                          <button
                            key={item.id}
                            type="button"
                            aria-pressed={style === item.id}
                            onClick={() => setStyle(item.id)}
                            className={cn(
                              "rounded-md border p-3 text-left",
                              style === item.id
                                ? "border-[#8b5cf6] bg-[#3b82f6]/14"
                                : "border-white/[0.08] hover:border-white/[0.18]",
                            )}
                          >
                            <span className="block text-xs font-medium">
                              {item.label}
                            </span>
                            <span className="mt-1 block text-[11px] leading-4 text-white/35">
                              {item.description}
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="max-colors">Цвета бренда</Label>
                      <Input
                        id="max-colors"
                        value={brandColors}
                        onChange={(event) => setBrandColors(event.target.value)}
                        placeholder="Например, графитовый и молочный"
                        className="rounded-md border-white/[0.14] bg-[#080a10]"
                        maxLength={120}
                      />
                    </div>
                  </div>
                </details>
              </div>

              <Button
                type="submit"
                size="xl"
                disabled={!ready || create.isPending}
                className="mt-7 w-full rounded-lg bg-[#3b82f6] hover:bg-[#2563eb]"
              >
                {create.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Создаём студию…
                  </>
                ) : (
                  <>
                    Создать MAX Mini App
                    <ArrowRight className="h-4 w-4" />
                  </>
                )}
              </Button>
              <p className="mt-3 text-center text-[11px] leading-4 text-white/30">
                Здесь создаются только мини-приложения для MAX. Технический стек
                и безопасная платформенная обвязка выбираются автоматически.
              </p>
            </form>
          </div>
        </section>

        <section className="mx-auto w-full max-w-[1440px] px-5 py-12 sm:px-8 lg:px-12 lg:py-16 xl:px-16">
          <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[#8b5cf6]">
                Рабочая область
              </p>
              <h2 className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">
                Мои MAX-приложения
              </h2>
              <p className="mt-2 text-sm text-white/40">
                Только MAX-проекты — обычные сайты здесь не показываются.
              </p>
            </div>
            <div className="flex items-center gap-2 border border-white/[0.12] px-3 py-1.5 text-xs text-white/45">
              <ShieldCheck className="h-3.5 w-3.5 text-[#8b5cf6]" />
              MAX Bridge и webhook уже в шаблоне
            </div>
          </div>

          {projects.isPending ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 3 }).map((_, index) => (
                <Skeleton key={index} className="h-72 rounded-2xl" />
              ))}
            </div>
          ) : projects.isError ? (
            <div className="rounded-2xl border border-danger/30 bg-danger/10 p-8 text-center text-sm text-white/60">
              Не удалось загрузить проекты. Обновите страницу и попробуйте ещё раз.
            </div>
          ) : maxProjects.length > 0 ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {maxProjects.map((project) => (
                <ProjectCard key={project.id} project={project} />
              ))}
            </div>
          ) : (
            <div className="grid gap-5 border border-dashed border-white/[0.16] p-7 sm:grid-cols-[1fr_auto] sm:items-center sm:p-10">
              <div>
                <h3 className="text-lg font-medium">Первого приложения пока нет</h3>
                <p className="mt-2 max-w-xl text-sm leading-6 text-white/40">
                  Заполните мастер выше — мы создадим MAX-проект, запустим
                  мобильное превью и сохраним его здесь.
                </p>
              </div>
              <Button asChild variant="secondary">
                <Link href="#top" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}>
                  Начать
                  <ArrowRight className="h-4 w-4" />
                </Link>
              </Button>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
