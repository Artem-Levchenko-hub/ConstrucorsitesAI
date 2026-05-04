import { Hero } from "@/components/marketing/Hero";
import { Features } from "@/components/marketing/Features";
import { Pricing } from "@/components/marketing/Pricing";
import { Faq } from "@/components/marketing/Faq";

export default function HomePage() {
  return (
    <>
      <Hero />
      <Features />
      <Pricing />
      <Faq />
    </>
  );
}
