import Link from "next/link";
import { Button } from "@/components/ui/button";

export function Header() {
  return (
    <header className="sticky top-0 z-40 border-b border-border-subtle bg-surface-base/80 backdrop-blur">
      <div className="mx-auto max-w-6xl px-6 h-14 flex items-center justify-between">
        <Link
          href="/"
          className="flex items-center gap-2 text-fg-primary font-semibold tracking-tight"
        >
          <span className="inline-block h-5 w-5 rounded-md bg-accent" />
          <span>Omnia.AI</span>
        </Link>

        <nav className="hidden md:flex items-center gap-6 text-sm text-fg-secondary">
          <Link href="#features" className="hover:text-fg-primary transition">
            Возможности
          </Link>
          <Link href="#pricing" className="hover:text-fg-primary transition">
            Цены
          </Link>
          <Link href="#faq" className="hover:text-fg-primary transition">
            FAQ
          </Link>
        </nav>

        <div className="flex items-center gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link href="/login">Войти</Link>
          </Button>
          <Button asChild size="sm">
            <Link href="/register">Начать</Link>
          </Button>
        </div>
      </div>
    </header>
  );
}
