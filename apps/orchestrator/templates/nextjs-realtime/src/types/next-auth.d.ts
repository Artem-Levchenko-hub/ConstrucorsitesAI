/**
 * Module augmentation so `auth()` / `useSession()` return a `user` carrying our
 * extra `id` and `role`. Type-check-only; no runtime impact.
 */

import type { DefaultSession } from "next-auth";

declare module "next-auth" {
  interface Session {
    user: {
      id: string;
      role: string;
    } & DefaultSession["user"];
  }
}
