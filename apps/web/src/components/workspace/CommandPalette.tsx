"use client";

/**
 * Global ⌘K command palette.
 *
 * Single dialog over a backdrop-blur veil. cmdk handles fuzzy matching
 * + keyboard navigation (↑↓/↵/esc) out of the box. We register actions
 * in three sections — Actions / Projects / Settings — sized to fit most
 * users without scroll. Project list is fetched once on first open and
 * cached via react-query so subsequent palette opens are instant.
 *
 * Architecture:
 * - Open state owned by `useCommandPalette` (Zustand) so anything in the
 *   app can pop the palette (e.g. an empty-state button "Press ⌘K").
 * - Action handlers are passed in by caller so the palette doesn't grow
 *   awareness of every subsystem (router / runtime mutations / etc.).
 * - cmdk's `Command.Empty` covers the no-match case so we don't render
 *   our own fallback UI per section.
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import {
  Github,
  Image as ImageIcon,
  Loader2,
  Pause,
  Play,
  Plus,
  Rocket,
  Search,
  Settings,
  Sparkles,
  Wallet,
} from "lucide-react";
import { create } from "zustand";
import { cn } from "@/lib/utils";
import { listProjects } from "@/lib/api/projects";
import { useWorkspaceStore } from "@/store/workspace";

interface CommandPaletteState {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
}

export const useCommandPalette = create<CommandPaletteState>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
  toggle: () => set((s) => ({ open: !s.open })),
}));

export function CommandPalette() {
  const open = useCommandPalette((s) => s.open);
  const setOpen = useCommandPalette((s) => s.setOpen);
  const toggle = useCommandPalette((s) => s.toggle);
  const router = useRouter();

  const toggleFocusMode = useWorkspaceStore((s) => s.toggleFocusMode);
  const toggleChat = useWorkspaceStore((s) => s.toggleChat);
  const toggleTimeline = useWorkspaceStore((s) => s.toggleTimeline);

  // Cmd/Ctrl + K toggles the palette. Lives at app-shell level so the
  // shortcut works from any page that mounts this component.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);

  // Fetch project list lazily — only when the palette opens for the
  // first time. Cached for 60 s so re-opening doesn't re-hit api.
  const { data: projects } = useQuery({
    queryKey: ["projects-list-palette"],
    queryFn: () => listProjects(),
    enabled: open,
    staleTime: 60_000,
  });

  // Reset search query when palette closes so reopening is a clean slate.
  const [search, setSearch] = useState("");
  useEffect(() => {
    if (!open) setSearch("");
  }, [open]);

  const run = (fn: () => void) => () => {
    setOpen(false);
    fn();
  };

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-label="Command palette"
      className="fixed inset-0 z-[100] flex items-start justify-center pt-[12vh] px-4 animate-[slide-up-fade_180ms_cubic-bezier(0.16,1,0.3,1)_both]"
      onClick={(e) => {
        if (e.target === e.currentTarget) setOpen(false);
      }}
    >
      {/* Backdrop — blur + tint so palette stands out without going pitch-black */}
      <div
        aria-hidden="true"
        className="absolute inset-0 bg-surface-base/40 backdrop-blur-md"
        onClick={() => setOpen(false)}
      />

      <Command
        label="Command palette"
        className="relative w-full max-w-2xl rounded-2xl border border-accent/30 bg-surface-base/85 backdrop-blur-2xl shadow-glow-accent overflow-hidden"
        shouldFilter
      >
        <div className="flex items-center gap-3 px-4 py-3.5 border-b border-border-subtle">
          <Search className="h-5 w-5 text-fg-tertiary" />
          <Command.Input
            value={search}
            onValueChange={setSearch}
            placeholder="Что хочешь сделать?"
            className="flex-1 bg-transparent text-base outline-none placeholder:text-fg-tertiary font-medium text-fg-primary"
            autoFocus
          />
          <kbd className="font-mono text-[11px] text-fg-tertiary px-1.5 py-0.5 rounded border border-border-default bg-surface-raised/60">
            esc
          </kbd>
        </div>

        <Command.List className="max-h-[400px] overflow-y-auto py-2">
          <Command.Empty className="px-4 py-8 text-center text-sm text-fg-tertiary">
            Ничего не нашёл по запросу{search ? ` «${search}»` : ""}.
          </Command.Empty>

          <Command.Group
            heading="Действия"
            className="[&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-mono [&_[cmdk-group-heading]]:text-fg-tertiary [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider"
          >
            <PaletteItem
              icon={<Rocket className="h-4 w-4 text-white" />}
              iconBg="linear-gradient(135deg,#6d4eff 0%,#b056ff 50%,#ec4cb8 100%)"
              iconGlow
              title="Опубликовать"
              hint="собрать prod-образ и переключить трафик"
              onSelect={run(() => {
                // Defer to TopBar's RuntimeButton — palette doesn't dispatch
                // the mutation directly to keep error handling / billing
                // / toast logic in one place. Simulate click via custom event.
                window.dispatchEvent(new CustomEvent("omnia:trigger-deploy"));
              })}
              keywords={["опубликовать", "deploy", "публикация", "прод"]}
            />
            <PaletteItem
              icon={<Pause className="h-4 w-4" />}
              title="Пауза dev-контейнера"
              hint="приостановить — будить за секунду"
              onSelect={run(() =>
                window.dispatchEvent(new CustomEvent("omnia:trigger-pause")),
              )}
              keywords={["пауза", "stop", "приостановить"]}
            />
            <PaletteItem
              icon={<Play className="h-4 w-4 text-success" />}
              iconBorder="border-success/30 bg-success/15"
              title="Запустить / разбудить"
              hint="поднять dev-контейнер"
              onSelect={run(() =>
                window.dispatchEvent(new CustomEvent("omnia:trigger-start")),
              )}
              keywords={["запустить", "wake", "start", "разбудить"]}
            />
            <PaletteItem
              icon={<Github className="h-4 w-4" />}
              iconBorder="border-border-default bg-surface-raised"
              title="Залить на GitHub"
              hint="push в подключённый репозиторий"
              onSelect={run(() =>
                window.dispatchEvent(new CustomEvent("omnia:trigger-github")),
              )}
              keywords={["github", "git", "push", "репозиторий"]}
            />
            <PaletteItem
              icon={<Sparkles className="h-4 w-4 text-accent" />}
              iconBorder="border-accent/30 bg-accent/15"
              title="Focus mode"
              hint="превью на весь экран — ⌘\\"
              onSelect={run(toggleFocusMode)}
              keywords={["focus", "фокус", "полный экран", "fullscreen"]}
            />
            <PaletteItem
              icon={<ImageIcon className="h-4 w-4" />}
              title="Свернуть / развернуть чат"
              hint="хоткей [ "
              onSelect={run(toggleChat)}
              keywords={["чат", "панель", "chat", "collapse"]}
            />
            <PaletteItem
              icon={<ImageIcon className="h-4 w-4" />}
              title="Свернуть / развернуть историю"
              hint="хоткей ]"
              onSelect={run(toggleTimeline)}
              keywords={["история", "timeline", "версии", "snapshots"]}
            />
          </Command.Group>

          <Command.Group
            heading="Перейти к проекту"
            className="[&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-mono [&_[cmdk-group-heading]]:text-fg-tertiary [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider"
          >
            <PaletteItem
              icon={<Plus className="h-4 w-4 text-accent" />}
              iconBorder="border-accent/30 bg-accent/15"
              title="Новый проект"
              hint="создать с нуля"
              onSelect={run(() => router.push("/projects"))}
              keywords={["новый", "create", "new", "проект"]}
            />
            {projects?.slice(0, 12).map((p) => (
              <PaletteItem
                key={p.id}
                icon={
                  <span className="text-xs font-mono font-bold">
                    {p.name?.[0]?.toUpperCase() ?? "·"}
                  </span>
                }
                iconBorder="border-border-default bg-surface-raised"
                title={p.name ?? p.slug}
                hint={`${p.template ?? "—"} · ${p.slug}`}
                onSelect={run(() => router.push(`/projects/${p.id}`))}
                keywords={[p.name ?? "", p.slug, p.template ?? ""].filter(
                  Boolean,
                )}
              />
            ))}
            {projects === undefined && (
              <div className="px-4 py-3 text-xs text-fg-tertiary flex items-center gap-2">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Загружаю список проектов…
              </div>
            )}
          </Command.Group>

          <Command.Group
            heading="Настройки"
            className="[&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-mono [&_[cmdk-group-heading]]:text-fg-tertiary [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider"
          >
            <PaletteItem
              icon={<Settings className="h-4 w-4" />}
              title="Аккаунт и интеграции"
              hint="GitHub, биллинг, профиль"
              onSelect={run(() => router.push("/account"))}
              keywords={["аккаунт", "settings", "профиль", "account"]}
            />
            <PaletteItem
              icon={<Wallet className="h-4 w-4 text-success" />}
              iconBorder="border-success/30 bg-success/15"
              title="Пополнить баланс"
              hint="ЮKassa / промокод"
              onSelect={run(() => router.push("/account#wallet"))}
              keywords={["баланс", "пополнить", "deposit", "топ-ап"]}
            />
          </Command.Group>
        </Command.List>

        <div className="px-4 py-2 border-t border-border-subtle bg-surface-base/40 flex items-center justify-between text-[10px] text-fg-tertiary font-mono">
          <div className="flex items-center gap-4">
            <span>
              <kbd className="px-1 py-0.5 rounded bg-surface-raised border border-border-default">
                ↑↓
              </kbd>{" "}
              навигация
            </span>
            <span>
              <kbd className="px-1 py-0.5 rounded bg-surface-raised border border-border-default">
                ↵
              </kbd>{" "}
              выбрать
            </span>
            <span>
              <kbd className="px-1 py-0.5 rounded bg-surface-raised border border-border-default">
                esc
              </kbd>{" "}
              закрыть
            </span>
          </div>
          <span className="text-fg-muted">Omnia.AI · ⌘K</span>
        </div>
      </Command>
    </div>
  );
}

interface PaletteItemProps {
  icon: React.ReactNode;
  iconBg?: string;
  iconBorder?: string;
  iconGlow?: boolean;
  title: string;
  hint?: string;
  onSelect: () => void;
  keywords?: string[];
}

function PaletteItem({
  icon,
  iconBg,
  iconBorder = "border-border-default bg-surface-raised/60",
  iconGlow = false,
  title,
  hint,
  onSelect,
  keywords = [],
}: PaletteItemProps) {
  // cmdk uses the `value` to filter; concat title+keywords so a user
  // typing in EN or RU both hit. `text-fg-primary` colour comes from
  // [data-selected="true"] state from cmdk.
  const value = `${title} ${keywords.join(" ")}`.toLowerCase();
  return (
    <Command.Item
      value={value}
      onSelect={onSelect}
      className="mx-1 px-3 py-2.5 rounded-lg flex items-center gap-3 cursor-pointer transition-colors data-[selected=true]:bg-accent/15 data-[selected=true]:ring-1 data-[selected=true]:ring-inset data-[selected=true]:ring-accent/30 hover:bg-surface-raised/40 text-fg-secondary data-[selected=true]:text-fg-primary"
    >
      <div
        className={cn(
          "h-8 w-8 rounded-lg flex items-center justify-center shrink-0 border",
          iconBorder,
          iconGlow && "shadow-glow-accent",
        )}
        style={iconBg ? { background: iconBg, borderColor: "transparent" } : undefined}
      >
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">{title}</div>
        {hint && (
          <div className="text-xs text-fg-tertiary truncate">{hint}</div>
        )}
      </div>
    </Command.Item>
  );
}
