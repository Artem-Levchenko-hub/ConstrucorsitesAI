"use client";

import { signOut } from "next-auth/react";

export function SignOutButton() {
  return (
    <button
      type="button"
      onClick={() => signOut({ callbackUrl: "/signin" })}
      className="rounded-md border border-neutral-700 px-2.5 py-1 text-neutral-300 transition hover:bg-neutral-800"
    >
      Выйти
    </button>
  );
}
