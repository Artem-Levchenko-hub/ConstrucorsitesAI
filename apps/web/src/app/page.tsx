import type { Metadata } from "next";

import { MaxPublicLanding } from "@/components/marketing/MaxPublicLanding";

export const metadata: Metadata = {
  title: "MAX Studio — приложение для MAX",
  description: "От идеи до проверяемого MAX-приложения в одном последовательном сценарии.",
};

export default function HomePage() {
  return <MaxPublicLanding />;
}
