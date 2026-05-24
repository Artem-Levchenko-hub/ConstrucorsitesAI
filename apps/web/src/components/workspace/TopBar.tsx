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
    <header className="shrink-0 h-14 flex items-center justify-between px-6 border-b border-border-default bg-surface-base">
      <div className="flex items-center gap-4 min-w-0">
        <Link
          href="/projects"
          className="flex items-center gap-2 text-fg-primary font-semibold tracking-tight"
        >
          <span className="inline-block h-5 w-5 rounded-md bg-accent" />
          <span className="hidden sm:inline">Omnia.AI</span>
        </Link>

        {projectName && (
          <>
            <span className="text-fg-tertiary">/</span>
            <span className="truncate text-sm font-medium">{projectName}</span>
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
                className="inline-flex items-center gap-1 h-7 px-2 rounded-md border border-border-default bg-surface-base text-xs text-fg-secondary whitespace-nowrap cursor-default select-none"
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
