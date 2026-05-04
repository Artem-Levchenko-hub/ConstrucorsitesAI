"use client";

import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const PROMPT_TEXT = "Сделай лендинг кофейни в Казани с меню и формой бронирования";

export function Hero() {
  const [typed, setTyped] = useState("");
  const [showWireframe, setShowWireframe] = useState(false);

  useEffect(() => {
    let i = 0;
    const interval = setInterval(() => {
      i += 1;
      setTyped(PROMPT_TEXT.slice(0, i));
      if (i >= PROMPT_TEXT.length) {
        clearInterval(interval);
        setTimeout(() => setShowWireframe(true), 350);
      }
    }, 35);
    return () => clearInterval(interval);
  }, []);

  return (
    <section className="relative overflow-hidden border-b border-border-subtle">
      <div className="mx-auto max-w-6xl px-6 py-24 grid lg:grid-cols-2 gap-12 items-center">
        <div className="space-y-6">
          <Badge variant="accent" className="font-mono">
            Beta · Запуск октябрь 2026
          </Badge>

          <h1 className="text-5xl font-semibold tracking-tight leading-[1.1]">
            Пиши промпты,
            <br />
            получай готовый сайт.
          </h1>

          <p className="text-fg-secondary text-base max-w-md">
            AI-сайт-билдер с backend, доменом и кнопкой «вернуться назад» для
            каждого промпта. Всё в рублях, всё на одной платформе.
          </p>

          <div className="flex flex-wrap gap-3 pt-2">
            <Button asChild size="lg">
              <Link href="/register">Начать бесплатно</Link>
            </Button>
            <Button asChild size="lg" variant="secondary">
              <Link href="#features">Как это работает</Link>
            </Button>
          </div>

          <p className="text-xs text-fg-tertiary font-mono pt-2">
            100 ₽ на счёт сразу после регистрации · без карты · без подписки
          </p>
        </div>

        <div className="relative h-[420px] rounded-lg border border-border-default bg-surface-raised overflow-hidden">
          <div className="absolute inset-0 flex flex-col">
            <div className="flex items-center gap-1.5 px-4 h-9 border-b border-border-subtle">
              <span className="w-3 h-3 rounded-full bg-border-strong" />
              <span className="w-3 h-3 rounded-full bg-border-strong" />
              <span className="w-3 h-3 rounded-full bg-border-strong" />
              <span className="ml-3 text-xs font-mono text-fg-tertiary">
                kofeynya-kazan.omnia.ai
              </span>
            </div>

            <div className="grid grid-cols-2 flex-1">
              <div className="border-r border-border-subtle p-4 flex flex-col gap-2">
                <div className="text-xs font-mono text-fg-tertiary">PROMPT</div>
                <div className="font-mono text-sm text-fg-primary leading-6 min-h-[120px]">
                  {typed}
                  <span className="inline-block w-[2px] h-[14px] -mb-0.5 bg-accent ml-0.5 animate-pulse" />
                </div>
              </div>

              <div className="p-4 flex items-center justify-center">
                <AnimatePresence>
                  {showWireframe && (
                    <motion.svg
                      initial={{ opacity: 0, y: 12 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
                      viewBox="0 0 220 260"
                      className="w-full h-full max-h-[300px]"
                    >
                      <rect
                        x="10"
                        y="10"
                        width="200"
                        height="20"
                        rx="3"
                        className="fill-surface-overlay"
                      />
                      <rect
                        x="14"
                        y="16"
                        width="40"
                        height="8"
                        rx="2"
                        className="fill-accent"
                      />
                      <motion.rect
                        initial={{ width: 0 }}
                        animate={{ width: 180 }}
                        transition={{ delay: 0.3, duration: 0.5 }}
                        x="20"
                        y="50"
                        height="14"
                        rx="2"
                        className="fill-fg-primary/80"
                      />
                      <motion.rect
                        initial={{ width: 0 }}
                        animate={{ width: 130 }}
                        transition={{ delay: 0.5, duration: 0.5 }}
                        x="20"
                        y="72"
                        height="8"
                        rx="2"
                        className="fill-fg-tertiary"
                      />
                      <motion.rect
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: 0.7 }}
                        x="20"
                        y="92"
                        width="60"
                        height="22"
                        rx="3"
                        className="fill-accent"
                      />
                      <motion.rect
                        initial={{ height: 0 }}
                        animate={{ height: 60 }}
                        transition={{ delay: 0.9, duration: 0.4 }}
                        x="20"
                        y="130"
                        width="80"
                        rx="3"
                        className="fill-surface-overlay"
                      />
                      <motion.rect
                        initial={{ height: 0 }}
                        animate={{ height: 60 }}
                        transition={{ delay: 1.0, duration: 0.4 }}
                        x="110"
                        y="130"
                        width="80"
                        rx="3"
                        className="fill-surface-overlay"
                      />
                      <motion.rect
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ delay: 1.2 }}
                        x="20"
                        y="206"
                        width="180"
                        height="38"
                        rx="3"
                        className="fill-surface-overlay"
                      />
                    </motion.svg>
                  )}
                </AnimatePresence>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
