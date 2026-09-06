import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";

import { ExternalDeployWizard } from "@/components/workspace/ExternalDeployWizard";

it("associates every VPS credential label with its own form control", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);

  try {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <ExternalDeployWizard projectId="project-a" maxStudio />
          <ExternalDeployWizard projectId="project-b" maxStudio />
        </QueryClientProvider>,
      );
    });

    const wizards = [
      ...container.querySelectorAll<HTMLElement>(
        '[data-testid="external-deploy-wizard"]',
      ),
    ];
    expect(wizards).toHaveLength(2);

    const expectedLabels = [
      "Домен",
      "Публичный IP VPS",
      "SSH-пользователь",
      "SSH-порт",
      "Пароль SSH",
    ];
    const controlIds = new Set<string>();

    for (const wizard of wizards) {
      for (const expectedLabel of expectedLabels) {
        const label = [...wizard.querySelectorAll<HTMLLabelElement>("label")].find(
          (candidate) => candidate.textContent?.trim() === expectedLabel,
        );
        expect(label, expectedLabel).toBeDefined();
        expect(label!.htmlFor, expectedLabel).not.toBe("");
        expect(wizard.contains(label!.control), expectedLabel).toBe(true);
        controlIds.add(label!.control!.id);
      }
    }
    expect(controlIds.size).toBe(expectedLabels.length * wizards.length);

    const keyButton = [...wizards[0].querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent?.includes("Приватный SSH-ключ"),
    );
    await act(async () => keyButton!.click());
    const keyLabel = [...wizards[0].querySelectorAll<HTMLLabelElement>("label")].find(
      (label) => label.textContent?.trim() === "Приватный ключ OpenSSH",
    );
    expect(keyLabel?.control?.tagName).toBe("TEXTAREA");
    expect(wizards[0].contains(keyLabel!.control)).toBe(true);
  } finally {
    await act(async () => root.unmount());
    client.clear();
    container.remove();
  }
});
