"use client";

import Link from "next/link";
import {
  ArrowLeft,
  ChevronDown,
  GitFork,
  LogOut,
  Settings,
  User as UserIcon,
} from "lucide-react";
import type { RemixSource } from "@/lib/project-lineage";
import { RemixSourceModal } from "./RemixSourceModal";
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
import { BuildExeButton } from "./BuildExeButton";
import { DownloadButton } from "./DownloadButton";
import { GithubPushButton } from "./GithubPushButton";
import { ImageGenToggle } from "./ImageGenToggle";
import { LogsViewer } from "./LogsViewer";
import { PublishButton } from "./PublishButton";
import { RuntimeButton } from "./RuntimeButton";
import { WalletBadge } from "./WalletBadge";

export function TopBar({
  user,
  projectName,
  projectId,
  projectSlug,
  imageGenEnabled,
  remixSource = null,
  importedRepoUrl = null,
  showProjectControls = true,
}: {
  user: { email: string };
  projectName?: string;
  /** V2 — required when showProjectControls is true so we can render the runtime button. */
  projectId?: string;
  /** Используется как default repo_name в диалоге «Залить в GitHub». */
  projectSlug?: string;
  /** V4 #3 — when set, the project is a remix of `remixSource`. Renders a
   *  clickable remix lineage badge next to the project name that opens a modal
   *  attributing the source + re-remixing this version (viral provenance). */
  remixSource?: RemixSource | null;
  /** B5+B6 — when set (non-null), shows a small «Импортировано из GitHub» badge
   *  linking to the original repository. Only passed when project.source==="imported". */
  importedRepoUrl?: string | null;
  /** Read-only: AI auto-classified design preset for this project. */
  designPresetId?: string;
  designPresetName?: string;
  /** Per-project: auto image-generation via gpt-image-1. Default true. */
  imageGenEnabled?: boolean;
  showProjectControls?: boolean;
}) {
  const initial = user.email.slice(0, 1).toUpperCase();

  return (
    <header className="shrink-0 h-14 flex items-center justify-between px-6 bg-[rgba(13,13,18,0.72)] backdrop-blur-xl">
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
            {remixSource && projectId && (
              <RemixSourceModal projectId={projectId} source={remixSource} />
            )}
            {importedRepoUrl && (
              <a
                href={importedRepoUrl}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1 rounded-full border border-border-subtle bg-surface-raised px-2 py-0.5 text-[11px] text-fg-tertiary hover:text-fg-secondary transition-colors shrink-0"
                title={importedRepoUrl}
              >
                <GitFork className="h-3 w-3" />
                GitHub
              </a>
            )}
          </>
        )}
      </div>

      <div className="flex items-center gap-2">
        {showProjectControls && (
          <>
            {projectId && <RuntimeButton projectId={projectId} />}
            {projectId && (
              <DownloadButton projectId={projectId} projectSlug={projectSlug} />
            )}
            {projectId && <BuildExeButton projectId={projectId} />}
            {projectId && <LogsViewer projectId={projectId} />}
            {projectSlug && <PublishButton projectSlug={projectSlug} />}
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
