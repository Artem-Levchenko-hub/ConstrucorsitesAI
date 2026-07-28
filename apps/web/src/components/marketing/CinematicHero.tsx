"use client";

import Link from "next/link";
import { ArrowRight, Play } from "lucide-react";
import {
  motion,
  useMotionTemplate,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
} from "framer-motion";
import type { PointerEvent as ReactPointerEvent } from "react";

import { Reveal } from "@/components/marketing/Reveal";
import { WordReveal } from "@/components/marketing/WordReveal";

interface CinematicHeroProps {
  badge: string;
  line1: string;
  line2: string;
  line3: string;
  subtitle: string;
  ctaPrimary: string;
  ctaDemo: string;
}

const ROTATION_SPRING = {
  stiffness: 120,
  damping: 20,
  mass: 0.95,
} as const;

const DRIFT_SPRING = {
  stiffness: 90,
  damping: 18,
  mass: 0.9,
} as const;

const proofPills = [
  "3D scene по курсору",
  "live preview без ожидания",
  "откат по версии за клик",
];

const versionItems = [
  { version: "v1.3", label: "hero camera", active: true },
  { version: "v1.2", label: "palette pass" },
  { version: "v1.1", label: "layout sync" },
];

export function CinematicHero({
  badge,
  line1,
  line2,
  line3,
  subtitle,
  ctaPrimary,
  ctaDemo,
}: CinematicHeroProps) {
  const reducedMotion = useReducedMotion();
  const pointerX = useMotionValue(0);
  const pointerY = useMotionValue(0);

  const rotateX = useSpring(
    useTransform(pointerY, [-1, 1], reducedMotion ? [0, 0] : [7, -7]),
    ROTATION_SPRING,
  );
  const rotateY = useSpring(
    useTransform(pointerX, [-1, 1], reducedMotion ? [0, 0] : [-9, 9]),
    ROTATION_SPRING,
  );

  const orbitX = useSpring(
    useTransform(pointerX, [-1, 1], reducedMotion ? [0, 0] : [-18, 18]),
    DRIFT_SPRING,
  );
  const orbitY = useSpring(
    useTransform(pointerY, [-1, 1], reducedMotion ? [0, 0] : [-16, 16]),
    DRIFT_SPRING,
  );

  const ambientX = useTransform(orbitX, (value) => value * 4.5);
  const ambientY = useTransform(orbitY, (value) => value * 4.5);
  const chipNearX = useTransform(orbitX, (value) => value * 0.95);
  const chipNearY = useTransform(orbitY, (value) => value * 0.8);
  const chipFarX = useTransform(orbitX, (value) => value * -0.7);
  const chipFarY = useTransform(orbitY, (value) => value * -0.55);
  const previewCopyX = useTransform(orbitX, (value) => value * 0.45);
  const previewCopyY = useTransform(orbitY, (value) => value * 0.38);
  const previewCardX = useTransform(orbitX, (value) => value * 0.72);
  const previewCardY = useTransform(orbitY, (value) => value * 0.62);
  const previewChromeX = useTransform(orbitX, (value) => value * -0.4);
  const previewChromeY = useTransform(orbitY, (value) => value * -0.3);

  const spotlight = useMotionTemplate`radial-gradient(circle at calc(50% + ${ambientX}px) calc(40% + ${ambientY}px), color-mix(in srgb, var(--color-accent) 34%, transparent), transparent 56%)`;
  const halo = useMotionTemplate`radial-gradient(circle at calc(50% + ${ambientX}px) calc(45% + ${ambientY}px), color-mix(in srgb, var(--color-label-1) 10%, transparent), transparent 62%)`;

  function handlePointerMove(event: ReactPointerEvent<HTMLElement>) {
    if (reducedMotion || event.pointerType !== "mouse") return;

    const rect = event.currentTarget.getBoundingClientRect();
    if (!rect.width || !rect.height) return;

    const x = (event.clientX - rect.left) / rect.width;
    const y = (event.clientY - rect.top) / rect.height;

    pointerX.set((x - 0.5) * 2);
    pointerY.set((y - 0.5) * 2);
  }

  function resetPointer() {
    pointerX.set(0);
    pointerY.set(0);
  }

  return (
    <section
      className="relative max-w-7xl mx-auto px-6 lg:px-12 pt-20 lg:pt-28 pb-20 lg:pb-24 overflow-hidden"
      onPointerMove={handlePointerMove}
      onPointerLeave={resetPointer}
    >
      <motion.div
        aria-hidden
        className="pointer-events-none absolute inset-x-[9%] top-[8%] bottom-[10%] rounded-[40px] opacity-80 blur-3xl"
        style={{ background: spotlight }}
      />
      <motion.div
        aria-hidden
        className="pointer-events-none absolute inset-x-[18%] top-[12%] bottom-[20%] rounded-[40px] opacity-55 blur-2xl"
        style={{ background: halo }}
      />
      <div
        className="hero-glow left-[-10%] top-[6%] h-[460px] w-[460px]"
        aria-hidden
      />
      <div
        className="hero-glow right-[-8%] top-[26%] h-[400px] w-[400px] [animation-delay:-7s]"
        aria-hidden
      />

      <div className="relative z-10 grid lg:grid-cols-12 gap-12 lg:gap-16 items-center">
        <div className="lg:col-span-7 space-y-7">
          <Reveal>
            <div className="inline-flex items-center gap-2 px-3 h-7 rounded-full border border-separator text-[12px] font-mono text-label-2 tabular-nums">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-system-green" />
              {badge}
            </div>
          </Reveal>

          <h1 className="text-[clamp(40px,5.8vw,76px)] leading-[0.98] tracking-[-0.035em] font-semibold text-balance">
            <WordReveal text={line1} className="block" />
            <WordReveal text={line2} className="block" baseDelay={0.18} />
            <WordReveal
              text={line3}
              className="block text-accent"
              baseDelay={0.36}
            />
          </h1>

          <Reveal delay={0.12}>
            <p className="text-[17px] leading-[1.55] text-label-2 max-w-xl">
              {subtitle}
            </p>
          </Reveal>

          <Reveal delay={0.2}>
            <div className="flex flex-wrap gap-3 pt-2">
              <Link
                href="/register"
                className="inline-flex items-center gap-2 h-12 px-6 rounded-full bg-accent text-accent-fg font-medium hover:bg-accent-hover active:scale-[0.98] transition-transform"
              >
                {ctaPrimary}
                <ArrowRight className="h-4 w-4" strokeWidth={2} />
              </Link>
              <a
                href="#demo"
                className="inline-flex items-center gap-2 h-12 px-6 rounded-full border border-separator-solid text-label-1 hover:border-label-3 active:scale-[0.98] transition-transform"
              >
                <Play className="h-4 w-4" strokeWidth={1.75} />
                {ctaDemo}
              </a>
            </div>
          </Reveal>

          <Reveal delay={0.26}>
            <div className="flex flex-wrap gap-2 pt-1">
              {proofPills.map((pill) => (
                <span
                  key={pill}
                  className="inline-flex items-center rounded-full border border-separator bg-bg-elevated-1/70 px-3 py-1.5 text-[11px] font-mono uppercase tracking-[0.12em] text-label-2"
                >
                  {pill}
                </span>
              ))}
            </div>
          </Reveal>

          <Reveal delay={0.32}>
            <div className="pt-4 text-[11px] font-mono uppercase tracking-[0.15em] text-label-3 tabular-nums">
              beeline · яндекс edu · skyeng · tinkoff · x5
            </div>
          </Reveal>
        </div>

        <Reveal delay={0.18} className="lg:col-span-5">
          <div className="relative mx-auto w-full max-w-[560px] [perspective:2200px]">
            <motion.div
              aria-hidden
              className="absolute -left-4 top-8 hidden md:block w-44 rounded-[22px] border border-separator bg-bg-base/80 p-4 shadow-[0_18px_60px_rgb(0_0_0_/_0.3)] backdrop-blur-xl"
              style={{ x: chipFarX, y: chipNearY, z: 110 }}
            >
              <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-label-3">
                prompt to scene
              </div>
              <div className="mt-2 text-[13px] font-semibold tracking-tight text-label-1">
                Камера слушается курсора
              </div>
              <div className="mt-2 text-[11px] leading-5 text-label-2">
                Свет, карточки и глубина двигаются отдельно, как интерфейсный
                кадр, а не плоский скриншот.
              </div>
            </motion.div>

            <motion.div
              aria-hidden
              className="absolute right-3 bottom-6 hidden md:block w-36 rounded-[20px] border border-separator bg-bg-elevated-1/80 p-3 shadow-[0_18px_60px_rgb(0_0_0_/_0.3)] backdrop-blur-xl"
              style={{ x: chipNearX, y: chipFarY, z: 105 }}
            >
              <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-label-3">
                runtime
              </div>
              <div className="mt-2 flex items-center justify-between text-[12px] text-label-2">
                <span>preview</span>
                <span className="text-system-green">live</span>
              </div>
              <div className="mt-1 flex items-center justify-between text-[12px] text-label-2">
                <span>deploy</span>
                <span className="text-label-1">ready</span>
              </div>
              <div className="mt-1 flex items-center justify-between text-[12px] text-label-2">
                <span>rollback</span>
                <span className="text-label-1">1 click</span>
              </div>
            </motion.div>

            <motion.div
              className="relative overflow-hidden rounded-[30px] border border-separator bg-bg-elevated-1/88 p-3 shadow-[0_35px_120px_rgb(0_0_0_/_0.42)] backdrop-blur-xl"
              style={{
                rotateX,
                rotateY,
                transformStyle: "preserve-3d",
              }}
            >
              <motion.div
                aria-hidden
                className="absolute inset-3 rounded-[24px] opacity-85"
                style={{ background: spotlight, z: -40 }}
              />
              <motion.div
                aria-hidden
                className="absolute inset-0 rounded-[30px] opacity-65"
                style={{
                  z: -55,
                  x: previewChromeX,
                  y: previewChromeY,
                  background:
                    "linear-gradient(135deg, color-mix(in srgb, var(--color-label-1) 10%, transparent), transparent 34%, color-mix(in srgb, var(--color-accent) 12%, transparent) 100%)",
                }}
              />

              <div className="relative overflow-hidden rounded-[26px] border border-separator bg-bg-elevated-1/96">
                <div className="material-thin border-b border-separator h-10 flex items-center justify-between px-4 text-[11px] font-mono text-label-2">
                  <div className="flex items-center gap-2">
                    <span className="inline-block h-2 w-2 rounded-full bg-system-green" />
                    <span className="text-label-1">cafe-polet</span>
                    <span className="text-label-3">/</span>
                    <span>main</span>
                  </div>
                  <div className="hidden md:flex items-center gap-2.5 tabular-nums">
                    <span>claude 4.5</span>
                    <span className="text-label-3">·</span>
                    <span>interactive build</span>
                    <span className="text-label-3">·</span>
                    <span className="text-accent">12 480 ₽</span>
                  </div>
                </div>

                <div className="grid grid-cols-[0.95fr_1.55fr_0.82fr] min-h-[420px]">
                  <motion.div
                    className="border-r border-separator p-3 space-y-2 text-[11px] overflow-hidden"
                    style={{ x: chipFarX, y: chipFarY, z: 38 }}
                  >
                    <div className="rounded-xl px-2.5 py-2 bg-accent text-accent-fg max-w-[86%] ml-auto">
                      лендинг кофейни в питере
                    </div>
                    <div className="rounded-xl px-2.5 py-2 bg-surface text-label-1 max-w-[94%]">
                      делаю hero с depth, мягким светом и mouse-driven камерой
                    </div>
                    <div className="flex gap-1.5 flex-wrap pt-0.5">
                      <span className="px-1.5 py-0.5 rounded-sm text-[9.5px] font-mono border border-separator text-label-2">
                        next.js
                      </span>
                      <span className="px-1.5 py-0.5 rounded-sm text-[9.5px] font-mono border border-separator text-label-2">
                        motion
                      </span>
                      <span className="px-1.5 py-0.5 rounded-sm text-[9.5px] font-mono border border-separator text-label-2">
                        premium ui
                      </span>
                    </div>
                    <div className="rounded-[16px] border border-separator bg-bg-base/70 p-3">
                      <div className="text-[10px] font-mono uppercase tracking-[0.16em] text-label-3">
                        cinematic pass
                      </div>
                      <div className="mt-2 h-1.5 rounded-full bg-surface">
                        <div className="h-full w-[72%] rounded-full bg-accent" />
                      </div>
                      <div className="mt-2 flex items-center justify-between text-[10px] font-mono text-label-2 tabular-nums">
                        <span>depth layers</span>
                        <span>72%</span>
                      </div>
                    </div>
                  </motion.div>

                  <div className="bg-bg-base p-2">
                    <motion.div
                      className="relative h-full overflow-hidden rounded-[22px] border border-separator"
                      style={{
                        x: previewChromeX,
                        y: previewChromeY,
                        z: 48,
                        background:
                          "linear-gradient(180deg, color-mix(in srgb, var(--color-bg-elevated-1) 92%, transparent), var(--color-bg-base))",
                      }}
                    >
                      <motion.div
                        aria-hidden
                        className="absolute inset-0 opacity-90"
                        style={{
                          x: ambientX,
                          y: ambientY,
                          z: -16,
                          background:
                            "radial-gradient(circle at top, color-mix(in srgb, var(--color-accent) 30%, transparent), transparent 58%)",
                        }}
                      />
                      <motion.div
                        className="material-ultrathin absolute inset-x-3 top-3 flex items-center gap-1.5 rounded-[14px] border border-separator px-2.5 py-2"
                        style={{ x: previewChromeX, y: previewChromeY, z: 74 }}
                      >
                        <span className="inline-block h-2 w-2 rounded-full bg-system-red" />
                        <span className="inline-block h-2 w-2 rounded-full bg-system-orange" />
                        <span className="inline-block h-2 w-2 rounded-full bg-system-green" />
                        <div className="mx-2 flex-1 rounded-full bg-bg-base/60 px-3 py-1 text-center text-[9px] font-mono text-label-3">
                          cafe-polet.omnia.app
                        </div>
                        <span className="inline-flex items-center gap-1 text-[9px] font-mono text-system-green">
                          <span className="inline-block h-1.5 w-1.5 rounded-full bg-system-green" />
                          live
                        </span>
                      </motion.div>

                      <div className="relative grid h-full grid-rows-[auto_1fr_auto] px-4 pb-4 pt-16">
                        <div className="flex items-center justify-between gap-3">
                          <motion.div
                            className="inline-flex items-center gap-2 rounded-full border border-separator bg-bg-base/55 px-3 py-1.5 text-[10px] font-mono uppercase tracking-[0.14em] text-label-2"
                            style={{ x: chipNearX, y: chipFarY, z: 70 }}
                          >
                            motion-led hero
                          </motion.div>
                          <motion.div
                            className="rounded-full bg-accent/15 px-3 py-1.5 text-[10px] font-mono uppercase tracking-[0.14em] text-accent"
                            style={{ x: chipFarX, y: chipNearY, z: 72 }}
                          >
                            deploy ready
                          </motion.div>
                        </div>

                        <div className="grid grid-cols-[1.05fr_0.95fr] gap-4 items-center">
                          <motion.div
                            className="space-y-4"
                            style={{ x: previewCopyX, y: previewCopyY, z: 92 }}
                          >
                            <div className="space-y-2">
                              <div className="text-[28px] font-semibold tracking-[-0.04em] leading-[0.95] text-label-1">
                                Cafe Polet
                              </div>
                              <div className="max-w-[18rem] text-[12px] leading-5 text-label-2">
                                Крафтовая обжарка, тихий свет, вечерний зал и
                                интерфейс, который собирает настроение, а не
                                просто блоки.
                              </div>
                            </div>
                            <div className="inline-flex h-9 items-center rounded-full bg-accent px-4 text-[11px] font-medium text-accent-fg">
                              забронировать столик
                            </div>
                            <div className="grid grid-cols-2 gap-2">
                              {[
                                ["scene", "3 layers"],
                                ["camera", "mouse-led"],
                                ["deploy", "1 click"],
                                ["rollback", "v1.3"],
                              ].map(([label, value]) => (
                                <div
                                  key={label}
                                  className="rounded-[16px] border border-separator bg-bg-base/55 px-3 py-2"
                                >
                                  <div className="text-[9px] font-mono uppercase tracking-[0.12em] text-label-3">
                                    {label}
                                  </div>
                                  <div className="mt-1 text-[12px] text-label-1">
                                    {value}
                                  </div>
                                </div>
                              ))}
                            </div>
                          </motion.div>

                          <motion.div
                            className="relative h-[250px]"
                            style={{ x: previewCardX, y: previewCardY, z: 96 }}
                          >
                            <motion.div
                              className="absolute inset-x-4 top-0 h-28 rounded-[22px] border border-separator bg-bg-elevated-1/88 p-3 shadow-[0_18px_50px_rgb(0_0_0_/_0.28)]"
                              style={{ x: chipNearX, y: chipFarY, z: 112 }}
                            >
                              <div className="flex items-center justify-between text-[10px] font-mono uppercase tracking-[0.12em] text-label-3">
                                <span>hero frame</span>
                                <span>depth</span>
                              </div>
                              <div className="mt-3 h-12 rounded-[16px] border border-separator bg-[linear-gradient(135deg,color-mix(in_srgb,var(--color-accent)_26%,transparent),transparent)]" />
                              <div className="mt-2 flex gap-2">
                                <div className="h-2.5 flex-1 rounded-full bg-surface" />
                                <div className="h-2.5 w-14 rounded-full bg-surface-2" />
                              </div>
                            </motion.div>
                            <motion.div
                              className="absolute inset-x-0 top-14 h-36 rounded-[24px] border border-separator bg-bg-base/92 p-4 shadow-[0_24px_60px_rgb(0_0_0_/_0.35)]"
                              style={{ x: chipFarX, y: chipNearY, z: 126 }}
                            >
                              <div className="flex items-center justify-between text-[10px] font-mono uppercase tracking-[0.12em] text-label-3">
                                <span>interactive cut</span>
                                <span>cursor</span>
                              </div>
                              <div className="mt-3 grid grid-cols-[1.1fr_0.9fr] gap-3">
                                <div className="space-y-2">
                                  <div className="h-3 rounded-full bg-label-1/90" />
                                  <div className="h-3 w-4/5 rounded-full bg-label-2/55" />
                                  <div className="h-3 w-3/5 rounded-full bg-label-3/45" />
                                  <div className="pt-2">
                                    <div className="inline-flex h-7 items-center rounded-full bg-accent px-3 text-[10px] font-medium text-accent-fg">
                                      открыть меню
                                    </div>
                                  </div>
                                </div>
                                <div className="rounded-[18px] border border-separator bg-bg-elevated-1/88" />
                              </div>
                            </motion.div>
                            <motion.div
                              className="absolute bottom-0 right-6 h-24 w-40 rounded-[22px] border border-separator bg-bg-elevated-1/82 p-3 shadow-[0_16px_40px_rgb(0_0_0_/_0.26)]"
                              style={{ x: chipNearX, y: chipNearY, z: 118 }}
                            >
                              <div className="text-[10px] font-mono uppercase tracking-[0.12em] text-label-3">
                                publish card
                              </div>
                              <div className="mt-2 h-2 rounded-full bg-surface" />
                              <div className="mt-2 h-8 rounded-[14px] border border-separator bg-bg-base/60" />
                              <div className="mt-2 flex items-center justify-between text-[10px] text-label-2">
                                <span>prod url</span>
                                <span className="text-system-green">ready</span>
                              </div>
                            </motion.div>
                          </motion.div>
                        </div>

                        <motion.div
                          className="mt-4 flex items-center gap-3 rounded-[18px] border border-separator bg-bg-base/55 px-3 py-2"
                          style={{ x: previewCopyX, y: chipNearY, z: 88 }}
                        >
                          <div className="h-2 w-2 rounded-full bg-system-green" />
                          <div className="text-[10px] font-mono uppercase tracking-[0.14em] text-label-2">
                            camera easing
                          </div>
                          <div className="h-1.5 flex-1 rounded-full bg-surface">
                            <div className="h-full w-[78%] rounded-full bg-accent" />
                          </div>
                          <div className="text-[10px] font-mono tabular-nums text-label-1">
                            78%
                          </div>
                        </motion.div>
                      </div>
                    </motion.div>
                  </div>

                  <motion.div
                    className="border-l border-separator p-2 space-y-2 text-[10px]"
                    style={{ x: chipNearX, y: chipFarY, z: 42 }}
                  >
                    {versionItems.map((item) => (
                      <div
                        key={item.version}
                        className={
                          item.active
                            ? "rounded-[16px] border border-accent/60 bg-accent/10 px-3 py-2"
                            : "rounded-[16px] border border-separator bg-bg-base/55 px-3 py-2"
                        }
                      >
                        <div className="font-mono text-[10px] tabular-nums text-label-1">
                          {item.version}
                        </div>
                        <div className="mt-1 text-[9px] text-label-2">
                          {item.label}
                        </div>
                      </div>
                    ))}
                    <div className="rounded-[16px] border border-separator bg-bg-base/55 p-3">
                      <div className="text-[9px] font-mono uppercase tracking-[0.12em] text-label-3">
                        scene notes
                      </div>
                      <div className="mt-2 text-[10px] leading-4 text-label-2">
                        Мягкий tilt, отдельная скорость у слоёв и световой halo
                        делают первый экран живым без перегруза.
                      </div>
                    </div>
                  </motion.div>
                </div>

                <div className="border-t border-separator h-8 flex items-center px-4 gap-2 text-[10px] font-mono text-label-2 tabular-nums">
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-system-green" />
                  <span>cinematic preview</span>
                  <span className="text-label-3">·</span>
                  <span>app/page.tsx</span>
                  <span className="text-label-3">·</span>
                  <span>motion tuned</span>
                  <span className="text-label-3 ml-auto">next.js · premium ui</span>
                </div>
              </div>
            </motion.div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
