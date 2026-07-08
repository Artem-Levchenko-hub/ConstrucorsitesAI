import Link from "next/link";

import { requireUser } from "@/lib/session";
import { SignOutButton } from "@/components/sign-out-button";

export const dynamic = "force-dynamic";

export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const user = await requireUser({ next: "/chat" });
  const initial = (user.email ?? "?").charAt(0).toUpperCase();
  return (
    <div className="app-canvas flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-border bg-card/70 px-4 py-2.5 backdrop-blur-sm">
        <Link
          href="/chat"
          className="flex items-center gap-2 font-semibold tracking-tight"
        >
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary text-primary-foreground text-sm font-bold shadow-sm">
            O
          </span>
          <span>Omnia Realtime</span>
        </Link>
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <span className="hidden items-center gap-2 sm:flex">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-muted text-xs font-medium text-foreground">
              {initial}
            </span>
            {user.email}
          </span>
          <SignOutButton />
        </div>
      </header>
      <main className="min-h-0 flex-1">{children}</main>
    </div>
  );
}
