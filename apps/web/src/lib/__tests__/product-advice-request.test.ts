import { expect, it, vi } from "vitest";
import { apiFetch } from "@/lib/api/client";
import { requestProductAdvice } from "@/lib/api/product-advice";

vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn().mockResolvedValue({ items: [] }) }));

it("allows the bounded AI analysis to finish before the browser times out", async () => {
  await requestProductAdvice("test-project");

  expect(apiFetch).toHaveBeenCalledWith("/api/projects/test-project/product-advice", {
    method: "POST",
    timeoutMs: 55_000,
  });
});
