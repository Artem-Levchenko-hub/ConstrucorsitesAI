import type { Metadata } from "next";

import { MaxPublicDemo } from "@/components/max/MaxPublicDemo";

export const metadata: Metadata = {
  title: "Создать демо MAX Mini App без регистрации",
  description:
    "Опишите бизнес и получите интерактивное демо мини-приложения для MAX до регистрации.",
};

export default function MaxDemoPage() {
  return <MaxPublicDemo />;
}
