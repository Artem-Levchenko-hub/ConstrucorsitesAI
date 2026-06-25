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
  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-neutral-800 px-4 py-2">
        <Link href="/chat" className="font-semibold tracking-tight">
          Omnia Realtime
        </Link>
        <div className="flex items-center gap-3 text-sm text-neutral-400">
          <span className="hidden sm:inline">{user.email}</span>
          <SignOutButton />
        </div>
      </header>
      <main className="min-h-0 flex-1">{children}</main>
    </div>
  );
}
