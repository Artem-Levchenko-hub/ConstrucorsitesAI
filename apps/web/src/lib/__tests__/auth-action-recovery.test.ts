import { describe, expect, it, vi } from "vitest";
import { recoverAuthAction } from "@/lib/auth-action-recovery";

const previous = { error: null };
function form() {
  const data = new FormData();
  data.set("email", "qa-owner@example.com");
  data.set("password", "synthetic-secret-123");
  data.set("next", "/max/qa-project");
  return data;
}

describe("auth form across a web release", () => {
  it("recovers a missing server action without replaying credentials", async () => {
    const action = vi.fn(async () => {
      throw Object.assign(new Error('Server Action "old-build-action" was not found on the server.'), { name: "UnrecognizedActionError" });
    });
    const data = form();
    const result = await recoverAuthAction(action)(previous, data);
    expect(result.refreshRequired).toBe(true);
    expect(result.error).toBe("Страница обновилась на сервере. Обновите её и войдите снова.");
    expect(action).toHaveBeenCalledTimes(1);
    expect(data.get("next")).toBe("/max/qa-project");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("retains normal validation errors without asking to reload", async () => {
    const result = await recoverAuthAction(async () => ({ error: "Неверный email или пароль" }))(previous, form());
    expect(result).toEqual({ error: "Неверный email или пароль" });
  });

  it("passes successful server redirect control flow through", async () => {
    const redirect = Object.assign(new Error("NEXT_REDIRECT"), { digest: "NEXT_REDIRECT;replace;/max;303;" });
    await expect(recoverAuthAction(async () => { throw redirect; })(previous, form())).rejects.toBe(redirect);
  });

  it("does not conceal ordinary transport or application errors", async () => {
    const error = new Error("Network response interrupted");
    await expect(recoverAuthAction(async () => { throw error; })(previous, form())).rejects.toBe(error);
  });

  it("handles the Next action-not-found message on an ordinary Error", async () => {
    const result = await recoverAuthAction(async () => { throw new Error('Server Action "old-action" was not found on the server. This request might be from an older or newer deployment.'); })(previous, form());
    expect(result.refreshRequired).toBe(true);
  });
});
