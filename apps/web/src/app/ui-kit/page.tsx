import type { Metadata } from "next";
import { UiKit } from "@/components/ui-kit/UiKit";
import "@/components/max/max-studio.css";
import "@/components/ui-kit/ui-kit.css";

export const metadata: Metadata = {
  title: "UI kit",
  description: "Компоненты Yleum и лаборатория движения.",
  alternates: { canonical: "/ui-kit" },
  robots: { index: false, follow: false },
};

export default function UiKitPage() {
  return <UiKit />;
}
