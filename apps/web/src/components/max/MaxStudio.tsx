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
      return { project, prompt };
    },
    onSuccess: ({ project, prompt }) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      toast.success("MAX Mini App создан", {
        description: "Открываем студию и запускаем первую сборку.",
      });
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
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-[#0b0c12] text-white">
      <MaxStudioHeader email={email} />

      <main className="max-studio-scroll flex-1 overflow-y-auto">
        <section
          id="top"
          className="relative isolate overflow-hidden border-b border-white/[0.07]"
        >
          <div className="pointer-events-none absolute inset-0 -z-10">
            <div className="absolute left-[12%] top-[-15rem] h-[32rem] w-[32rem] rounded-full bg-[#635bff]/20 blur-[110px]" />
            <div className="absolute right-[-8rem] top-[18rem] h-[26rem] w-[26rem] rounded-full bg-[#1eb8ff]/10 blur-[120px]" />
            <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.025)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.025)_1px,transparent_1px)] bg-[size:64px_64px] [mask-image:linear-gradient(to_bottom,black,transparent_82%)]" />
          </div>

          <div className="mx-auto grid w-full max-w-[1380px] gap-12 px-5 py-12 sm:px-8 lg:grid-cols-[minmax(0,1.05fr)_minmax(400px,0.95fr)] lg:py-16 xl:gap-20">
            <div className="flex flex-col justify-center">
              <div className="mb-6 inline-flex w-fit items-center gap-2 rounded-full border border-[#7368ff]/30 bg-[#7368ff]/10 px-3 py-1.5 text-xs font-medium text-[#b8b2ff]">
                <Sparkles className="h-3.5 w-3.5" />
                От идеи до приложения внутри MAX
              </div>
              <h1 className="max-w-3xl text-4xl font-semibold leading-[0.98] tracking-[-0.045em] sm:text-5xl xl:text-[68px]">
                Соберите MAX Mini App без технического задания
              </h1>
              <p className="mt-6 max-w-2xl text-base leading-7 text-white/55 sm:text-lg">
                Ответьте на несколько продуктовых вопросов. Студия сама создаст
                безопасное мини-приложение, подготовит сервер, публикацию и
                подключение MAX-бота.
              </p>

              <div className="mt-9 grid max-w-2xl gap-3 sm:grid-cols-3">
                {[
                  {
                    Icon: WandSparkles,
                    title: "1. Опишите идею",
                    text: "Без промптов и терминов",
                  },
                  {
                    Icon: Rocket,
                    title: "2. Получите приложение",
                    text: "Сразу в мобильном превью",
                  },
                  {
                    Icon: Bot,
                    title: "3. Подключите MAX",
                    text: "Бот и webhook в одном месте",
                  },
                ].map(({ Icon, title, text }) => (
                  <div
                    key={title}
                    className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-4"
                  >
                    <Icon className="mb-4 h-5 w-5 text-[#8d83ff]" />
                    <div className="text-sm font-medium">{title}</div>
                    <div className="mt-1 text-xs leading-5 text-white/40">{text}</div>
                  </div>
                ))}
              </div>
            </div>

            <form
              className="rounded-[28px] border border-white/[0.1] bg-[#13151e]/90 p-5 shadow-[0_30px_100px_-40px_rgba(0,0,0,0.9)] backdrop-blur-xl sm:p-7"
              onSubmit={(event) => {
                event.preventDefault();
                if (ready && !create.isPending) create.mutate();
              }}
            >
              <div className="mb-7 flex items-start justify-between gap-4">
                <div>
                  <p className="text-xs font-medium uppercase tracking-[0.18em] text-[#8d83ff]">
                    Новый проект
                  </p>
                  <h2 className="mt-2 text-2xl font-semibold tracking-tight">
                    Что создаём?
                  </h2>
                </div>
                <div className="rounded-xl border border-white/[0.08] bg-white/[0.04] p-2.5 text-white/45">
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
                      className="h-11 border-white/[0.1] bg-black/20"
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
                      className="min-h-24 resize-none border-white/[0.1] bg-black/20"
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
                            "rounded-xl border p-3 text-left transition-all",
                            active
                              ? "border-[#7468ff] bg-[#7468ff]/12 shadow-[0_0_0_1px_rgba(116,104,255,0.15)]"
                              : "border-white/[0.08] bg-white/[0.025] hover:border-white/[0.18] hover:bg-white/[0.04]",
                          )}
                        >
                          <span className="flex items-center justify-between gap-2">
                            <span className="text-sm font-medium">{item.label}</span>
                            {active && <Check className="h-4 w-4 text-[#9a91ff]" />}
                          </span>
                          <span className="mt-1 block text-xs leading-4 text-white/40">
                            {item.description}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </fieldset>

                <details className="group rounded-2xl border border-white/[0.08] bg-white/[0.025]">
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
                          className="border-white/[0.1] bg-black/20"
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
                          className="border-white/[0.1] bg-black/20"
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
                                "rounded-full border px-3 py-1.5 text-xs transition-colors",
                                active
                                  ? "border-[#7468ff]/60 bg-[#7468ff]/15 text-white"
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
                              "rounded-xl border p-3 text-left",
                              style === item.id
                                ? "border-[#7468ff] bg-[#7468ff]/12"
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
                        placeholder="Например, фиолетовый и молочный"
                        className="border-white/[0.1] bg-black/20"
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
                className="mt-7 w-full rounded-xl bg-[linear-gradient(135deg,#7569ff,#5c5cff)] shadow-[0_14px_36px_-14px_rgba(105,95,255,0.9)] hover:brightness-110"
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

        <section className="mx-auto w-full max-w-[1380px] px-5 py-12 sm:px-8 lg:py-16">
          <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.18em] text-[#8d83ff]">
                Рабочая область
              </p>
              <h2 className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">
                Мои MAX-приложения
              </h2>
              <p className="mt-2 text-sm text-white/40">
                Только MAX-проекты — обычные сайты здесь не показываются.
              </p>
            </div>
            <div className="flex items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-xs text-white/45">
              <ShieldCheck className="h-3.5 w-3.5 text-[#8d83ff]" />
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
            <div className="grid gap-5 rounded-[28px] border border-dashed border-white/[0.12] bg-white/[0.02] p-7 sm:grid-cols-[1fr_auto] sm:items-center sm:p-10">
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
