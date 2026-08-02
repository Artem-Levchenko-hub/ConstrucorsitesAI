import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const read = (path: string) =>
  readFileSync(resolve(process.cwd(), path), "utf8");

const globals = read("src/app/globals.css");
const accountShell = read("src/components/account/AccountShell.tsx");
const maxStudio = read("src/components/max/MaxStudio.tsx");
const maxStudioAccountDisclosure = read(
  "src/components/max/MaxStudioAccountDisclosure.tsx",
);
const projectCard = read("src/components/max/MaxStudioProjectCard.tsx");
const deleteDialog = read("src/components/projects/DeleteProjectDialog.tsx");
const mvpPage = read("src/app/mvp/page.tsx");
const startPage = read("src/app/max/start/page.tsx");
const guidePage = read("src/app/max/guide/page.tsx");
const publishWorkspace = read("src/components/max/MaxPublishWorkspace.tsx");

describe("MAX product identity and project management", () => {
  it("derives a restrained matte UI palette from the official MAX brand family", () => {
    expect(globals).toContain("--color-max-brand-blue: #471aff");
    expect(globals).toContain("--color-max-brand-violet: #6e1aff");
    expect(globals).toContain("--color-max-brand-cyan: #00bfff");
    expect(globals).toContain("--color-max-brand-purple: #9500ff");
    expect(globals).toContain("--color-max-blue: #554fc4");
    expect(globals).toContain("--color-max-violet: #756db2");
    expect(globals).toContain("--color-max-cyan: #4e91a6");
    expect(globals).toContain("--color-max-ink: #17162c");
    expect(globals).toContain("--color-accent: #554fc4");
    expect(globals).toContain("--color-accent-hover: #4742aa");
    expect(globals).toContain("--color-accent-on-dark: #9e99e5");
    expect(globals).toContain("[data-graphite-shell] .text-accent");
    expect(mvpPage).toContain("bg-accent-on-dark");
    expect(startPage).toContain("<section data-graphite-shell");
    expect(guidePage).toContain("<div data-graphite-shell");
    expect(publishWorkspace).toContain("<aside data-graphite-shell");
  });

  it("keeps account pages inside the same light MAX Studio shell", () => {
    expect(accountShell).toContain("border-r border-border-default bg-surface-raised");
    expect(accountShell).toContain("MAX Studio · Настройки");
    expect(accountShell).toContain("bg-surface-3 font-medium text-fg-primary");
    expect(accountShell).toContain("flex h-11 items-center");
    expect(accountShell).not.toContain("data-graphite-shell");
  });

  it("offers safe project deletion directly on MAX project cards", () => {
    expect(projectCard).toContain("<DeleteProjectDialog");
    expect(projectCard).toContain("<DropdownMenuTrigger asChild>");
    expect(projectCard).toContain("Действия с проектом ${project.name}");
    expect(projectCard).toContain("Удалить проект");
    expect(projectCard).toContain("className=\"size-11");
    expect(projectCard).toContain("successFocusRef={successFocusRef}");
    expect(deleteDialog).toContain("disabled={mutation.isPending}");
    expect(deleteDialog).toContain('queryKey: ["projects"], exact: true');
    expect(deleteDialog).toContain("qc.setQueryData<Project[]>");
    expect(deleteDialog).toContain("void qc.invalidateQueries");
    expect(deleteDialog).toContain("onCloseAutoFocus");
    expect(deleteDialog).toContain("deletedRef.current ? successFocusRef : returnFocusRef");
    expect(deleteDialog).toContain("targetRef.current?.isConnected");
    expect(maxStudio).toContain("ref={projectsHeadingRef} tabIndex={-1}");
  });

  it("keeps billing in the account disclosure without a duplicate Studio entry", () => {
    expect(maxStudio).not.toContain('<Link href="/billing"');
    expect(maxStudioAccountDisclosure).toContain('["/billing", WalletCards');
    expect(maxStudio).not.toContain('/account?tab=billing');
    expect(maxStudio).toContain('aria-label="Найти проект"');
    expect(maxStudio).toContain('name="project-search"');
  });
});
