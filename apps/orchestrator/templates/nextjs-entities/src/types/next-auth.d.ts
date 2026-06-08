/**
 * Module-augmentation so `useSession()` / `auth()` return a `user` with
 * our extra `id` and `role` fields. Auth.js merges this into its own
 * `Session` interface at type-check time only — no runtime impact.
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
