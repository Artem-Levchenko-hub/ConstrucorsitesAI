"use client";

import Link from "next/link";
import { ChevronDown, LogOut, Settings, User as UserIcon } from "lucide-react";
import { logoutAction } from "@/app/(auth)/actions";
import {
  Avatar,
  AvatarFallback,
} from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { GithubPushButton } from "./GithubPushButton";
import { ImageGenToggle } from "./ImageGenToggle";
import { ModelSelector } from "./ModelSelector";
import { RuntimeButton } from "./RuntimeButton";
import { WalletBadge } from "./WalletBadge";

export function TopBar({
  user,
  projectName,
  projectId,
  projectSlug,
  designPresetId,
  designPresetName,
  imageGenEnabled,
  showProjectControls = true,
}: {
  user: { email: string };
  projectName?: string;
  /** V2 — required when showProjectControls is true so we can render the runtime button. */
  projectId?: string;
  /** Используется как default repo_name в диалоге «Залить в GitHub». */
  projectSlug?: string;
  /** Read-only: AI auto-classified design preset for this project. */
  designPresetId?: string;
  designPresetName?: string;
  /** Per-project: auto image-generation via gpt-image-1. Default true. */
  imageGenEnabled?: boolean;
  showProjectControls?: boolean;
}) {
  const initial = user.email.slice(0, 1).toUpperCase();

  return (
    <header className="shrink-0 h-14 flex items-center justify-between px-6 border-b border-border-subtle bg-[rgba(13,13,18,0.72)] backdrop-blur-xl relative">
      {/* Hair-thin accent line under the border so the top edge isn't dead-flat.
          Echoes the body aurora at low opacity. */}
      <div
        aria-hidden="true"
        className="absolute inset-x-0 -bottom-px h-px bg-gradient-to-r from-transparent via-accent/40 to-transparent pointer-events-none"
      />

      <div className="flex items-center gap-4 min-w-0">
        <Link
          href="/projects"
          className="flex items-center gap-2.5 font-semibold tracking-tight group"
          aria-label="Omnia.AI — мои проекты"
        >
          {/* Logo mark — gradient square with a large soft halo that breathes
              behind it. The halo is bigger than the mark (h-10 vs h-6) and
              blurred heavily so the violet glow is unmistakable. Negative
              positioning keeps the mark itself aligned to the original 24 px
              footprint — no layout shift, only ambient light. */}
          <span className="relative inline-flex h-6 w-6 items-center justify-center">
            <span
              aria-hidden="true"
              className="absolute -inset-2 rounded-full bg-[radial-gradient(circle,rgba(124,92,255,0.7)_0%,rgba(124,92,255,0.25)_45%,transparent_70%)] blur-lg animate-breathe-glow"
            />
            <span className="relative h-6 w-6 rounded-lg bg-[linear-gradient(135deg,#7c5cff_0%,#a48aff_100%)] shadow-[0_6px_20px_-4px_rgba(124,92,255,0.7)] transition-transform group-hover:scale-110" />
          </span>
          <span className="hidden sm:inline text-gradient-accent">Omnia.AI</span>
        </Link>

        {projectName && (
          <>
            <span
              aria-hidden="true"
              className="text-fg-muted text-sm font-light select-none"
            >
              /
            </span>
            <span className="truncate text-sm font-medium text-fg-primary">
              {projectName}
            </span>
          </>
        )}
      </div>

      <div className="flex items-center gap-2">
        {showProjectControls && (
          <>
            {projectId && <RuntimeButton projectId={projectId} />}
            {projectId && projectSlug && (
              <GithubPushButton
                projectId={projectId}
                projectSlug={projectSlug}
              />
            )}
            {projectId && (
              <ImageGenToggle
                projectId={projectId}
                imageGenEnabled={imageGenEnabled ?? true}
              />
            )}
            <ModelSelector />
            {designPresetId && designPresetName && (
              <span
                title={`Дизайн-пресет: ${designPresetName}. AI выбрал автоматически.`}
                className="inline-flex items-center gap-1 h-7 px-2.5 rounded-full border border-border-default bg-surface-raised text-xs text-fg-secondary whitespace-nowrap cursor-default select-none"
              >
                <span aria-hidden="true">🎨</span>
                <span className="truncate max-w-[140px]">{designPresetName}</span>
              </span>
            )}
            <WalletBadge />
          </>
        )}

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="gap-2 px-1.5">
              <Avatar className="h-7 w-7">
                <AvatarFallback>{initial}</AvatarFallback>
              </Avatar>
              <ChevronDown className="h-3.5 w-3.5 text-fg-tertiary" />
            </Button>
          </DropdownMenuTrigger>

          <DropdownMenuContent align="end">
            <DropdownMenuLabel className="font-normal">
              <div className="text-xs text-fg-tertiary">Вошли как</div>
              <div className="text-sm text-fg-primary truncate max-w-[200px]">
                {user.email}
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/projects">
                <UserIcon className="h-4 w-4" />
                Мои проекты
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/account">
                <Settings className="h-4 w-4" />
                Аккаунт
              </Link>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <form action={logoutAction} className="w-full">
                <button type="submit" className="flex items-center gap-2 w-full">
                  <LogOut className="h-4 w-4" />
                  Выйти
                </button>
              </form>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
