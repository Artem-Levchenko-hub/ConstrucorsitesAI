import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { newPasswordError, passwordLength, PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH } from "@/lib/password-policy";

const cases: { id: string; password: string; accepted: boolean }[] = JSON.parse(readFileSync(
  resolve(process.cwd(), "../api/tests/fixtures/password_policy_cases.json"), "utf8"));

it.each(cases)("matches API policy for $id", ({ password, accepted }) => {
  expect(newPasswordError(password) === null).toBe(accepted);
});
it("counts Unicode code points and does not normalize a chosen password", () => {
  expect(passwordLength("🦊🌿🍂🐚🌙moss river")).toBe(PASSWORD_MIN_LENGTH);
  expect(passwordLength(cases.find(item => item.id === "maximum_unicode")!.password)).toBe(PASSWORD_MAX_LENGTH);
  expect(newPasswordError("  moss river blue  ")).toBeNull();
});
