import { describe, expect, it } from "vitest";

import type { DeployTarget } from "@/lib/api/deploy-targets";
import type { CustomDomain } from "@/lib/api/domains";
import {
  ExternalDeployDnsError,
  launchExternalDeploy,
  type ExternalDeployDependencies,
} from "@/lib/api/external-deploy";
import type { DeployStatus } from "@/lib/api/types";

const target: DeployTarget = {
  id: "target-1",
  label: "Production VPS",
  ssh_host: "203.0.113.10",
  ssh_port: 22,
  ssh_user: "deploy",
  auth_type: "password",
  has_secret: true,
  ssh_public_key: null,
  verify_status: "ok",
  verify_detail: "ready",
  host_fingerprint: "SHA256:test",
  resolved_ip: "203.0.113.10",
  capabilities: null,
  verified_at: "2026-07-29T00:00:00Z",
  created_at: "2026-07-29T00:00:00Z",
};

const domain: CustomDomain = {
  id: "domain-1",
  project_id: "project-1",
  host: "shop.example.ru",
  source: "external",
  expected_ip: "203.0.113.10",
  dns_status: "pending",
  cert_status: "none",
  last_detail: null,
  created_at: "2026-07-29T00:00:00Z",
  verified_at: null,
  dns_instructions: null,
};

const deploy: DeployStatus = {
  run_id: "run-1",
  phase: "building",
  started_at: "2026-07-29T00:00:00Z",
  finished_at: null,
  prod_url: null,
  image_tag: null,
  error: null,
  detail: "building",
  target_label: "Production VPS",
  target_id: "target-1",
  can_cancel: true,
  logs: [],
};

function dependencies(
  overrides: Partial<ExternalDeployDependencies> = {},
  calls: string[] = [],
): ExternalDeployDependencies {
  return {
    setProjectDeployTarget: async () => {
      calls.push("select");
      return {};
    },
    listDomains: async () => {
      calls.push("list-domains");
      return [];
    },
    connectDomain: async () => {
      calls.push("connect-domain");
      return domain;
    },
    checkDomain: async () => {
      calls.push("check-domain");
      return { ...domain, dns_status: "ok" };
    },
    issueDomainCert: async () => {
      calls.push("issue-domain");
      return {
        ...domain,
        dns_status: "ok",
        last_detail: "HTTPS will be configured during deploy",
      };
    },
    deployProject: async () => {
      calls.push("deploy");
      return deploy;
    },
    ...overrides,
  };
}

describe("launchExternalDeploy", () => {
  it("selects the VPS and deploys directly when no domain was entered", async () => {
    const calls: string[] = [];

    const result = await launchExternalDeploy(
      { projectId: "project-1", target },
      dependencies({}, calls),
    );

    expect(result.domain).toBeNull();
    expect(result.deploy).toBe(deploy);
    expect(calls).toEqual(["select", "deploy"]);
  });

  it("connects, proves and prepares the domain before starting deploy", async () => {
    const calls: string[] = [];

    await launchExternalDeploy(
      {
        projectId: "project-1",
        target,
        domainHost: "Shop.Example.RU.",
      },
      dependencies({}, calls),
    );

    expect(calls).toEqual([
      "select",
      "list-domains",
      "connect-domain",
      "check-domain",
      "issue-domain",
      "deploy",
    ]);
  });

  it("reuses an existing domain instead of creating a duplicate", async () => {
    const calls: string[] = [];

    await launchExternalDeploy(
      {
        projectId: "project-1",
        target,
        domainHost: "shop.example.ru",
      },
      dependencies(
        {
          listDomains: async () => {
            calls.push("list-domains");
            return [domain];
          },
        },
        calls,
      ),
    );

    expect(calls).not.toContain("connect-domain");
    expect(calls.at(-1)).toBe("deploy");
  });

  it("stops before deploy while the registrar A-record is not ready", async () => {
    const calls: string[] = [];
    const mismatch = {
      ...domain,
      dns_status: "mismatch" as const,
      last_detail: "A-запись указывает на другой IP.",
    };

    await expect(
      launchExternalDeploy(
        {
          projectId: "project-1",
          target,
          domainHost: domain.host,
        },
        dependencies(
          {
            checkDomain: async () => {
              calls.push("check-domain");
              return mismatch;
            },
          },
          calls,
        ),
      ),
    ).rejects.toEqual(new ExternalDeployDnsError(mismatch));

    expect(calls).not.toContain("issue-domain");
    expect(calls).not.toContain("deploy");
  });
});
