"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowRight, Menu, X } from "lucide-react";
import { ShinyText } from "./ShinyText";

const VIDEO_URL =
  "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260328_105406_16f4600d-7a92-4292-b96e-b19156c7830a.mp4";

const NAV_LINKS = [
  { href: "/", label: "Главная" },
  { href: "#features", label: "Возможности" },
  { href: "#templates", label: "Шаблоны" },
  { href: "#instructors", label: "Команда" },
  { href: "#testimonials", label: "Отзывы" },
  { href: "/blog", label: "Блог" },
];

export function VideoHero() {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <section className="relative h-screen w-full overflow-hidden bg-black font-sans">
      <video
        className="absolute inset-0 h-full w-full object-cover"
        src={VIDEO_URL}
        autoPlay
        loop
        muted
        playsInline
        preload="auto"
        aria-hidden="true"
      />
      {/* Apple-style depth: top vignette for nav legibility, bottom for CTA */}
      <div
        className="absolute inset-0 bg-gradient-to-b from-black/55 via-black/20 to-black/65"
        aria-hidden="true"
      />
      <div
        className="absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t from-black/70 to-transparent"
        aria-hidden="true"
      />

      <div className="relative z-10 flex h-full flex-col">
        <nav className="mx-auto w-full max-w-7xl px-6 pt-6 lg:px-8">
          <div className="flex items-center justify-between">
            <Link
              href="/"
              className="flex items-center gap-2.5 text-white"
              aria-label="Omnia.AI — на главную"
            >
              <span className="flex h-7 w-7 items-center justify-center rounded-full border border-white/50">
                <span className="h-2 w-2 rounded-full bg-white" />
              </span>
              <span className="text-base font-medium tracking-tight">
                Omnia.AI
              </span>
            </Link>

            <div className="hidden lg:flex items-center rounded-full border border-white/10 bg-white/[0.04] px-1.5 py-1 backdrop-blur-xl">
              {NAV_LINKS.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  className="rounded-full px-3.5 py-1.5 text-[13px] font-medium tracking-tight text-white/75 transition-colors hover:bg-white/[0.06] hover:text-white"
                >
                  {link.label}
                </Link>
              ))}
              <Link
                href="#contact"
                className="ml-1 inline-flex items-center gap-1.5 rounded-full bg-white/[0.08] px-3.5 py-1.5 text-[13px] font-medium tracking-tight text-white/90 transition-colors hover:bg-white/[0.14] hover:text-white"
              >
                Контакты
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </div>

            <button
              type="button"
              onClick={() => setMobileOpen((v) => !v)}
              className="lg:hidden inline-flex h-9 w-9 items-center justify-center rounded-full border border-white/15 bg-white/[0.05] text-white backdrop-blur-xl"
              aria-label={mobileOpen ? "Закрыть меню" : "Открыть меню"}
              aria-expanded={mobileOpen}
            >
              {mobileOpen ? (
                <X className="h-4 w-4" />
              ) : (
                <Menu className="h-4 w-4" />
              )}
            </button>
          </div>

          {mobileOpen && (
            <div className="lg:hidden mt-3 rounded-2xl border border-white/10 bg-black/60 p-3 backdrop-blur-xl">
              <div className="flex flex-col">
                {NAV_LINKS.map((link) => (
                  <Link
                    key={link.href}
                    href={link.href}
                    onClick={() => setMobileOpen(false)}
                    className="rounded-lg px-3 py-2 text-sm text-white/80 hover:bg-white/5 hover:text-white"
                  >
                    {link.label}
                  </Link>
                ))}
                <Link
                  href="#contact"
                  onClick={() => setMobileOpen(false)}
                  className="mt-1 inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm text-white/90 hover:bg-white/5 hover:text-white"
                >
                  Контакты
                  <ArrowRight className="h-3.5 w-3.5" />
                </Link>
              </div>
            </div>
          )}
        </nav>

        <div className="mx-auto w-full max-w-7xl px-6 pt-10 lg:px-8 lg:pt-14">
          <div className="grid gap-6 lg:grid-cols-2 lg:gap-12">
            <p className="max-w-md text-sm text-white/70 md:text-[15px] leading-relaxed">
              Опиши идею промптом — Omnia соберёт сайт, бэкенд, домен и хостинг.
              Любую версию можно вернуть в один клик.
            </p>
            <p className="text-sm text-white/70 md:text-[15px] leading-relaxed lg:text-right">
              1 200+ сайтов уже запущено на платформе.
            </p>
          </div>
        </div>

        <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col items-center justify-center px-6 text-center lg:px-8">
          <p className="mb-6 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-[11px] font-medium uppercase tracking-[0.18em] text-white/80 backdrop-blur-md md:mb-7 md:text-xs">
            <span className="h-1.5 w-1.5 rounded-full bg-white shadow-[0_0_8px_rgba(255,255,255,0.8)]" />
            Beta · набор первых пользователей
          </p>

          <h1
            className="font-medium text-white text-5xl sm:text-6xl md:text-7xl lg:text-8xl xl:text-9xl"
            style={{ lineHeight: 0.88, letterSpacing: "-0.045em" }}
          >
            <span className="block">Опиши.</span>
            <ShinyText
              text="Получи сайт."
              baseColor="#A5C8FF"
              shineColor="#ffffff"
              speed={4.5}
              spread={110}
              className="block"
            />
          </h1>

          <div className="mt-10 flex flex-col sm:flex-row items-center gap-3 md:mt-12">
            <Link
              href="/register"
              className="group inline-flex items-center gap-2 rounded-full bg-white px-6 py-3 text-sm font-medium text-black transition-all hover:bg-white/90 hover:shadow-[0_8px_32px_-8px_rgba(255,255,255,0.4)] md:px-7 md:py-3.5 md:text-[15px]"
            >
              Начать бесплатно
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
            </Link>
            <Link
              href="#features"
              className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-6 py-3 text-sm font-medium text-white/90 backdrop-blur-md transition-colors hover:border-white/25 hover:bg-white/[0.08] md:px-7 md:py-3.5 md:text-[15px]"
            >
              Посмотреть демо
            </Link>
          </div>
        </div>

        <div className="h-12 md:h-16" aria-hidden="true" />
      </div>
    </section>
  );
}
