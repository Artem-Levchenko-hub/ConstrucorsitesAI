import {
  setProjectDeployTarget,
  type DeployTarget,
} from "@/lib/api/deploy-targets";
import {
  checkDomain,
  connectDomain,
  issueDomainCert,
  listDomains,
  type CustomDomain,
} from "@/lib/api/domains";
import { deployProject } from "@/lib/api/runtime";
import type { DeployStatus } from "@/lib/api/types";

export class ExternalDeployDnsError extends Error {
  readonly domain: CustomDomain;

  constructor(domain: CustomDomain) {
    super(
      domain.last_detail ??
        `A-запись ${domain.host} ещё не указывает на ${domain.expected_ip}.`,
    );
    this.name = "ExternalDeployDnsError";
    this.domain = domain;
  }
}

export type ExternalDeployDependencies = {
  setProjectDeployTarget: typeof setProjectDeployTarget;
  listDomains: typeof listDomains;
  connectDomain: typeof connectDomain;
  checkDomain: typeof checkDomain;
  issueDomainCert: typeof issueDomainCert;
  deployProject: typeof deployProject;
};

const defaultDependencies: ExternalDeployDependencies = {
  setProjectDeployTarget,
  listDomains,
  connectDomain,
  checkDomain,
  issueDomainCert,
  deployProject,
};

export type ExternalDeployResult = {
  target: DeployTarget;
  domain: CustomDomain | null;
  deploy: DeployStatus;
};

/**
 * The atomic user journey after SSH fingerprint confirmation.
 *
 * Each server-side operation remains independently idempotent/recoverable, but
 * the UI gets one deep operation: choose the verified VPS, attach/reuse the
 * domain, prove its A-record, mark HTTPS ready, then start the durable deploy.
 */
export async function launchExternalDeploy(
  {
    projectId,
    target,
    domainHost,
  }: {
    projectId: string;
    target: DeployTarget;
    domainHost?: string;
  },
  dependencies: ExternalDeployDependencies = defaultDependencies,
): Promise<ExternalDeployResult> {
  await dependencies.setProjectDeployTarget(projectId, target.id);

  const normalizedHost = domainHost?.trim().toLowerCase().replace(/\.$/, "");
  let domain: CustomDomain | null = null;
  if (normalizedHost) {
    const currentDomains = await dependencies.listDomains(projectId);
    domain =
      currentDomains.find((item) => item.host === normalizedHost) ??
      (await dependencies.connectDomain(projectId, normalizedHost));
    domain = await dependencies.checkDomain(domain.id);
    if (domain.dns_status !== "ok") {
      throw new ExternalDeployDnsError(domain);
    }
    domain = await dependencies.issueDomainCert(domain.id);
  }

  const deploy = await dependencies.deployProject(projectId);
  return { target, domain, deploy };
}
