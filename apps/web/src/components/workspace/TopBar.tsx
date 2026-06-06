"use client";

import Link from "next/link";
import {
  ArrowLeft,
  ChevronDown,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Settings,
  User as UserIcon,
} from "lucide-react";
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
import { DesignPresetSelector } from "./DesignPresetSelector";
import { GithubPushButton } from "./GithubPushButton";
import { ImageGenToggle } from "./ImageGenToggle";
import { LogsViewer } from "./LogsViewer";
import { RuntimeButton } from "./RuntimeButton";
import { WalletBadge } from "./WalletBadge";
import { useWorkspaceStore } from "@/store/workspace";

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
  const chatCollapsed = useWorkspaceStore((s) => s.chatCollapsed);
  const timelineCollapsed = useWorkspaceStore((s) => s.timelineCollapsed);
  const toggleChat = useWorkspaceStore((s) => s.toggleChat);
  const toggleTimeline = useWorkspaceStore((s) => s.toggleTimeline);

  return (
    <header className="shrink-0 h-14 flex items-center justify-between px-6 border-b border-border-subtle bg-[rgba(13,13,18,0.72)] backdrop-blur-xl">
      <div className="flex items-center gap-4 min-w-0">
        <Link
          href="/projects"
          className="flex items-center gap-2 text-fg-primary font-semibold tracking-tight"
        >
          <span className="inline-block h-6 w-6 rounded-lg bg-[linear-gradient(135deg,#7c5cff_0%,#a48aff_100%)] shadow-[0_4px_12px_-2px_rgba(124,92,255,0.5)]" />
          <span className="hidden sm:inline">Omnia.AI</span>
        </Link>

        {projectName && (
          <>
            <span className="text-fg-tertiary">/</span>
            <Link
              href="/projects"
              className="flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-sm text-fg-secondary transition-colors hover:bg-surface-overlay hover:text-fg-primary"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Проекты
            </Link>
            <span className="text-fg-tertiary">/</span>
            <span className="truncate text-sm font-medium">{projectName}</span>
          </>
        )}
      </div>

      <div className="flex items-center gap-2">
        {showProjectControls && (
          <>
            <div className="mr-1 flex items-center gap-0.5">
              <Button
                variant="ghost"
                size="sm"
                className="px-1.5"
                onClick={toggleChat}
                aria-label={chatCollapsed ? "Показать чат" : "Свернуть чат"}
                title={chatCollapsed ? "Показать чат" : "Свернуть чат"}
              >
                {chatCollapsed ? (
                  <PanelLeftOpen className="h-4 w-4 text-fg-tertiary" />
                ) : (
                  <PanelLeftClose className="h-4 w-4 text-fg-tertiary" />
                )}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="px-1.5"
                onClick={toggleTimeline}
                aria-label={
                  timelineCollapsed ? "Показать историю" : "Свернуть историю"
                }
                title={timelineCollapsed ? "Показать историю" : "Свернуть историю"}
              >
                {timelineCollapsed ? (
                  <PanelRightOpen className="h-4 w-4 text-fg-tertiary" />
                ) : (
                  <PanelRightClose className="h-4 w-4 text-fg-tertiary" />
                )}
              </Button>
            </div>
            {projectId && <RuntimeButton projectId={projectId} />}
            {projectId && <LogsViewer projectId={projectId} />}
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
            {projectId && (
              <DesignPresetSelector
                projectId={projectId}
                initialPresetId={designPresetId}
                initialPresetName={designPresetName}
              />
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
