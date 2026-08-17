import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const accountMenu = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxAccountMenu.tsx"),
  "utf8",
);
const studioDisclosure = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxStudioAccountDisclosure.tsx"),
  "utf8",
);
const workspace = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxWorkspaceShell.tsx"),
  "utf8",
);
const studio = readFileSync(
  resolve(process.cwd(), "src/components/max/MaxStudio.tsx"),
  "utf8",
);

const accountLinks = [
  "/account",
  "/account/organization",
  "/account/security",
  "/billing",
  "/billing/transactions",
  "/billing/plan",
] as const;

describe("MAX account menus", () => {
  it("exposes all existing account destinations behind one disclosure", () => {
    for (const href of accountLinks) {
      expect(accountMenu).toContain(`"${href}"`);
      expect(studioDisclosure).toContain(`"${href}"`);
    }
    expect(workspace).toContain("<MaxAccountMenu");
    expect(studio).toContain("<MaxStudioAccountDisclosure />");
  });

  it("keeps keyboard and focus-safe close behavior", () => {
    expect(accountMenu).toContain('event.key !== "Escape"');
    expect(accountMenu).toContain("triggerRef.current?.focus()");
    expect(accountMenu).toContain('aria-expanded={open}');
    expect(accountMenu).toContain("closeOnOutsidePress");
    expect(studioDisclosure).toContain('inert={!open}');
    expect(studioDisclosure).toContain('tabIndex={open ? 0 : -1}');
  });
});
