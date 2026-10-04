"use client";
import { useQuery } from "@tanstack/react-query";
import { getProject } from "@/lib/api/projects";
import type { DeployStatus } from "@/lib/api/types";
import { getMaxPublicationGuard } from "@/lib/max-publication-guard";

export function useMaxPublicationGuard(projectId: string, deployment: DeployStatus | undefined) {
  const needsIdentity = deployment?.phase === "failed" && deployment.reason_code === "migration_required";
  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => getProject(projectId),
    enabled: needsIdentity, retry: false, refetchInterval: needsIdentity ? 10_000 : false });
  return getMaxPublicationGuard(deployment, project.isSuccess ? project.data : undefined, projectId);
}
