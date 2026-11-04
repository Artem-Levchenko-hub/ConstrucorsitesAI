import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

import { OAuthConsentForm } from "@/components/auth/OAuthConsentForm";

const action = vi.hoisted(() => vi.fn(async (_previous: unknown, _data: FormData) => ({ error: "Тестовый отказ" })));
vi.mock("@/app/(auth)/actions", () => ({ oauthCompleteAction: action }));
afterEach(() => vi.clearAllMocks());

it.each(["terms_accepted", "privacy_accepted", "personal_data_accepted"])("keeps %s required, localized and independent before forwarding the OAuth ticket", async missing => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  try {
    await act(async () => root.render(<OAuthConsentForm ticket="fixture-ticket" email="example@example.test" providerLabel="Тест" next="/max" documentVersion="fixture-version" />));
    const form = container.querySelector("form")!;
    const box = form.querySelector<HTMLInputElement>(`input[name="${missing}"]`)!;
    for (const other of form.querySelectorAll<HTMLInputElement>('input[type="checkbox"][required]')) if (other !== box) await act(async () => other.click());
    await act(async () => form.requestSubmit());
    expect(action).not.toHaveBeenCalled(); expect(box.validationMessage).toContain("галочку");
    expect(document.getElementById(box.getAttribute("aria-describedby")!)?.textContent).toContain("галочку");
    await act(async () => box.click()); expect(box.validationMessage).toBe("");
    await act(async () => box.click());
    await act(async () => form.requestSubmit());
    expect(action).not.toHaveBeenCalled(); expect(box.validationMessage).toContain("галочку");
    await act(async () => box.click());
    await act(async () => form.requestSubmit());
    expect(action).toHaveBeenCalledTimes(1);
    const data = action.mock.calls[0][1];
    expect(data.get("ticket")).toBe("fixture-ticket"); expect(data.get("next")).toBe("/max"); expect(data.get("document_version")).toBe("fixture-version");
    for (const name of ["terms_accepted", "privacy_accepted", "personal_data_accepted"]) expect(data.get(name)).toBe("on");
    expect(data.has("marketing_accepted")).toBe(false);
  } finally { await act(async () => root.unmount()); container.remove(); }
});
