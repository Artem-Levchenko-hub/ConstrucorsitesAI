import type { MaxReadiness } from "@/lib/api/types";

export type MaxLaunchStepStatus = "completed" | "current" | "upcoming";

export type MaxLaunchWizardStep = MaxReadiness["items"][number] & {
  position: number;
  status: MaxLaunchStepStatus;
  instruction: string;
};

const STEP_INSTRUCTIONS: Record<string, string> = {
  business: "Добавьте описание продукта и контакты поддержки в настройках приложения.",
  legal: "Заполните данные оператора и подтвердите обязательные условия.",
  build: "Завершите текущую сборку приложения в чате.",
  bot: "Подключите секрет промодерированного бота для безопасной проверки пользователей MAX.",
  publish: "Запустите публикацию: студия подготовит постоянный HTTPS-адрес.",
  webhook: "Webhook настраивается автоматически после успешной публикации.",
  max_url: "Добавьте HTTPS-адрес в кабинете MAX и подтвердите привязку здесь.",
};

export function getMaxLaunchStepInstruction(itemId: string): string {
  return STEP_INSTRUCTIONS[itemId] ?? "Выполните это действие, чтобы перейти к следующему шагу.";
}

export async function copyMaxLaunchUrl(
  url: string,
  writeText: (value: string) => Promise<void> = (value) =>
    navigator.clipboard.writeText(value),
): Promise<boolean> {
  try {
    await writeText(url);
    return true;
  } catch {
    return false;
  }
}

/** Turns server readiness checks into one ordered, screen-reader-friendly wizard. */
export function getMaxLaunchWizard(items: MaxReadiness["items"]): {
  completedCount: number;
  currentStep: MaxLaunchWizardStep | undefined;
  steps: MaxLaunchWizardStep[];
  total: number;
} {
  const currentIndex = items.findIndex((item) => !item.done);
  const completedCount = items.filter((item) => item.done).length;

  return {
    completedCount,
    currentStep:
      currentIndex === -1
        ? undefined
        : {
            ...items[currentIndex],
            position: currentIndex + 1,
            status: "current",
            instruction: getMaxLaunchStepInstruction(items[currentIndex].id),
          },
    steps: items.map((item, index) => ({
      ...item,
      position: index + 1,
      status: item.done ? "completed" : index === currentIndex ? "current" : "upcoming",
      instruction: getMaxLaunchStepInstruction(item.id),
    })),
    total: items.length,
  };
}
