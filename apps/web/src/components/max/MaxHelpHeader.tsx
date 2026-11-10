import { ArrowLeft, BookOpenText, Rocket } from "lucide-react";
import Link from "next/link";
import type { ComponentProps } from "react";

import { BrandMark } from "@/components/marketing/BrandMark";
import { Button } from "@/components/ui/button";

export function MaxHelpHeader({ brand, guide = false }: {
  brand: ComponentProps<typeof BrandMark>;
  guide?: boolean;
}) {
  return (
    <header className={guide ? "max-topbar sticky top-0 z-50" : "max-topbar"}>
      <div className="flex min-w-0 items-center gap-3">
        <BrandMark {...brand} />
        {guide && <span className="hidden truncate border-l border-border-default pl-3 text-sm text-fg-secondary lg:block">Руководство Yleum</span>}
      </div>
      <nav aria-label="Навигация по справке" className="max-topbar-actions">
        {guide ? (
          <>
            <Button asChild variant="secondary" size="nav" className="hidden sm:inline-flex">
              <Link href="/max/product"><ArrowLeft aria-hidden="true" />О продукте</Link>
            </Button>
            <Button asChild variant="secondary" size="nav" className="max-[380px]:w-11 max-[380px]:px-0">
              <Link href="/max/register"><Rocket aria-hidden="true" /><span className="max-[380px]:sr-only">Начать</span></Link>
            </Button>
          </>
        ) : (
          <Button asChild variant="secondary" size="nav" className="max-[480px]:w-11 max-[480px]:px-0">
            <Link href="/max/guide"><BookOpenText aria-hidden="true" /><span className="max-[480px]:sr-only">Полное руководство</span></Link>
          </Button>
        )}
        <Button asChild size="nav" className="max-[360px]:px-2">
          <Link href="/max">Открыть Yleum</Link>
        </Button>
      </nav>
    </header>
  );
}
