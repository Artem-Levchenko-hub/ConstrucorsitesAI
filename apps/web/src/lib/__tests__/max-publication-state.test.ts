import { describe, expect, it } from "vitest";

import type { MaxReadiness } from "@/lib/api/types";
import { getMaxPublicationState } from "@/lib/max-publication-state";

function readiness(published: boolean): MaxReadiness {
  return {
    ready_to_launch: false,
    progress: published ? 80 : 60,
    items: [
      {
        id: "publish",
        label: "Текущая версия доступна по HTTPS",
        done: published,
        blocking: true,
        action: "Опубликовать",
      },
    ],
  };
}

describe("MAX publication state", () => {
  it("waits for the canonical readiness response", () => {
    expect(getMaxPublicationState(undefined, "done")).toBe("checking");
  });

  it("marks only the current ready snapshot as published", () => {
    expect(getMaxPublicationState(readiness(true), "done")).toBe("published");
  });

  it("does not present an old successful deploy as the current publication", () => {
    expect(getMaxPublicationState(readiness(false), "done")).toBe("outdated");
  });

  it("keeps projects without a successful deploy unpublished", () => {
    expect(getMaxPublicationState(readiness(false), "failed")).toBe("unpublished");
  });
});
