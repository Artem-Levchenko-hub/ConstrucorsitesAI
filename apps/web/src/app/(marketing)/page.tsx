import { VideoHero } from "@/components/marketing/VideoHero";
import { Features } from "@/components/marketing/Features";
import { Pricing } from "@/components/marketing/Pricing";
import { Faq } from "@/components/marketing/Faq";

export default function HomePage() {
  return (
    <>
      <VideoHero />
      <Features />
      <Pricing />
      <Faq />
    </>
  );
}
