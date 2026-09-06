import type { Metadata } from "next";

import { MaxPublicLanding } from "@/components/marketing/MaxPublicLanding";

export const metadata: Metadata = {
  title: "MAX Studio — приложение для MAX",
  description: "От идеи до проверяемого MAX-приложения в одном последовательном сценарии.",
  alternates: { canonical: "/max/product" },
};

export default function MaxProductPage() {
  return <MaxPublicLanding />;
}
