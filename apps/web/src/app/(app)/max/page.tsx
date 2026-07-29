import type { Metadata } from "next";

import { MaxStudio } from "@/components/max/MaxStudio";
import { getSession } from "@/lib/auth-mock";

export const metadata: Metadata = {
  title: "MAX Studio — конструктор мини-приложений",
  description:
    "Создайте мини-приложение для мессенджера MAX: мобильный интерфейс, бот, webhook и публикация в одном сценарии.",
  robots: { index: false, follow: false },
};

export default async function MaxStudioPage() {
  const session = await getSession();
  if (!session) return null;

  return <MaxStudio email={session.email} />;
}
