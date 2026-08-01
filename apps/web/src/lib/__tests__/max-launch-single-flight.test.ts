import { describe, expect, it, vi } from "vitest";

import { runMaxLaunchSingleFlight } from "@/lib/max-launch-single-flight";

describe("MAX launch single-flight", () => {
  it("reuses one active launch for the same project", async () => {
    let finish!: (value: string) => void;
    const task = vi.fn(
      () => new Promise<string>((resolve) => {
        finish = resolve;
      }),
    );

    const first = runMaxLaunchSingleFlight("project-a", task);
    const second = runMaxLaunchSingleFlight("project-a", task);

    expect(second).toBe(first);
    expect(task).toHaveBeenCalledTimes(1);

    finish("done");
    await expect(first).resolves.toBe("done");
  });

  it("allows a new launch after the previous one settles", async () => {
    const task = vi.fn(async () => "done");

    await runMaxLaunchSingleFlight("project-b", task);
    await runMaxLaunchSingleFlight("project-b", task);

    expect(task).toHaveBeenCalledTimes(2);
  });
});
