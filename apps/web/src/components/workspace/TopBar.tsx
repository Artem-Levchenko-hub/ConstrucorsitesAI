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
import { useTranslations } from "next-intl";
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
import { DeploySettingsButton } from "./DeploySettingsButton";
import { DownloadButton } from "./DownloadButton";
import { GithubPushButton } from "./GithubPushButton";
import { ImageGenToggle } from "./ImageGenToggle";
import { LeadsButton } from "./LeadsButton";
import { LogsViewer } from "./LogsViewer";
import { MaxIntegrationButton } from "./MaxIntegrationButton";
import { PublishButton } from "./PublishButton";
import { RuntimeButton } from "./RuntimeButton";
import { WalletBadge } from "./WalletBadge";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { BrandMark } from "@/components/marketing/BrandMark";

export function TopBar({
  user,
  projectName,
  projectId,
  projectSlug,
  imageGenEnabled,
  remixSource = null,
  importedRepoUrl = null,
  projectTemplate,
  showProjectControls = true,
  workspaceMode = "default",
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
  projectTemplate?: string;
  showProjectControls?: boolean;
  workspaceMode?: "default" | "max";
}) {
  const initial = user.email.slice(0, 1).toUpperCase();
  const tNav = useTranslations("nav");
  const maxMode = workspaceMode === "max";

  return (
    <header className="flex h-16 shrink-0 items-center justify-between gap-3 border-b border-[#25272b] bg-[#121519]/95 px-5 backdrop-blur-xl sm:px-6">
      <div className="flex shrink-0 items-center gap-4">
        <BrandMark inverse href={maxMode ? "/max" : "/projects"} />
        {maxMode && (
          <>
            <span className="h-5 w-px bg-white/20" aria-hidden />
            <Link
              href="/max"
              className="hidden text-xs font-semibold uppercase tracking-[0.12em] text-slate-500 hover:text-white sm:inline"
            >
              MAX Studio
            </Link>
          </>
        )}

        {projectName && (
          <>
            {/* Back-to-projects breadcrumb — redundant with the logo link, so it
                only appears on very wide screens; compact view is just
                «Omnia.AI / <project>». */}
            <span className="hidden 2xl:inline text-fg-tertiary">/</span>
            <Link
              href={maxMode ? "/max" : "/projects"}
              className="hidden 2xl:flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-sm text-fg-secondary transition-colors hover:bg-surface-overlay hover:text-fg-primary"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              <span>{maxMode ? "MAX-приложения" : tNav("projects")}</span>
            </Link>
            <span className="text-fg-tertiary">/</span>
            <span className="max-w-[14rem] truncate text-sm font-semibold text-slate-100">{projectName}</span>
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

      {/* Middle: project action buttons. They SCROLL horizontally when the toolbar
          is narrower than their total width, instead of overflowing onto the logo
          (the old single right-cluster couldn't shrink, so the min-w-0 left cluster
          collapsed to 0 and the buttons rendered on top of the brand). */}
      <div className="flex min-w-0 flex-1 items-center justify-end gap-2 overflow-x-auto omnia-no-scrollbar">
        {showProjectControls && (
          <>
            {!maxMode && (
              <>
                {projectId && <RuntimeButton projectId={projectId} />}
                {projectId && <DeploySettingsButton projectId={projectId} />}
                {projectId && (
                  <MaxIntegrationButton
                    projectId={projectId}
                    initialTemplate={projectTemplate}
                  />
                )}
                {projectId && (
                  <DownloadButton projectId={projectId} projectSlug={projectSlug} />
                )}
                {projectId && <BuildExeButton projectId={projectId} />}
                {projectId && <LogsViewer projectId={projectId} />}
                {projectSlug && <PublishButton projectSlug={projectSlug} />}
                {projectId && <LeadsButton projectId={projectId} />}
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
              </>
            )}
          </>
        )}
      </div>

      {/* Right rail: balance + language + account stay ALWAYS visible — never part
          of the scroll — so the user can always reach the wallet/account menu. */}
      <div className="flex shrink-0 items-center gap-1.5 pl-1">
        {showProjectControls && <WalletBadge />}

        <LocaleSwitcher inverse />

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="group gap-1.5 rounded-full border border-white/15 bg-white/[0.05] px-1.5 text-white hover:border-white/30 hover:bg-white/[0.10] hover:text-white data-[state=open]:border-white/35 data-[state=open]:bg-white/[0.12]"
              aria-label={`${tNav("account")}: ${user.email}`}
            >
              <Avatar className="h-7 w-7 ring-1 ring-white/20">
                <AvatarFallback className="bg-accent font-semibold text-accent-fg">
                  {initial}
                </AvatarFallback>
              </Avatar>
              <ChevronDown className="h-3.5 w-3.5 text-slate-300 transition-transform group-hover:text-white group-data-[state=open]:rotate-180 group-data-[state=open]:text-white" />
            </Button>
          </DropdownMenuTrigger>

          <DropdownMenuContent align="end">
            <DropdownMenuLabel className="font-normal">
              <div className="text-xs text-fg-tertiary">{tNav("loggedInAs")}</div>
              <div className="text-sm text-fg-primary truncate max-w-[200px]">
                {user.email}
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/projects">
                <UserIcon className="h-4 w-4" />
                {tNav("myProjects")}
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/account">
                <Settings className="h-4 w-4" />
                {tNav("account")}
              </Link>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <form action={logoutAction} className="w-full">
                <button type="submit" className="flex items-center gap-2 w-full">
                  <LogOut className="h-4 w-4" />
                  {tNav("logout")}
                </button>
              </form>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
