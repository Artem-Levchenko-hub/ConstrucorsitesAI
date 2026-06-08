/**
 * Auth.js (NextAuth v5) configuration — pre-wired with the Drizzle adapter
 * and a Credentials (email + password) provider so generated apps can
 * sign users in out of the box. AI doesn't need to touch this file —
 * extending session shape or adding OAuth providers happens here.
 *
 * Architecture:
 * - Session strategy: `jwt` (required by the Credentials provider — Auth.js
 *   rejects a credentials sign-in under the `database` strategy). The token
 *   carries id + role and expires 30 days from sign-in; rotate AUTH_SECRET to
 *   revoke everyone.
 * - Password hashing: bcryptjs (10 rounds). The hash lives in
 *   `users.passwordHash`; the plaintext password NEVER reaches the DB.
 * - `auth.config.ts` would split edge-vs-node concerns if the project
 *   ever needs middleware-level auth. Today middleware uses cookie
 *   probing, see `src/middleware.ts`.
 *
 * The orchestrator injects two env vars on container start:
 * - `AUTH_SECRET` — used by Auth.js for session token signing. Generated
 *   per-project at provision time; rotation invalidates all sessions.
 * - `DATABASE_URL` — already documented elsewhere.
 *
 * Adding a new OAuth provider:
 * 1. `pnpm add` the relevant `@auth/<provider>` package
 * 2. Import the provider here, add to `providers[]`
 * 3. Set the provider's CLIENT_ID / CLIENT_SECRET env vars
 * The `accounts` table already supports any provider Auth.js ships.
 */

import bcrypt from "bcryptjs";
import { eq } from "drizzle-orm";
import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import { DrizzleAdapter } from "@auth/drizzle-adapter";
import { z } from "zod";

import { db } from "@/lib/db";
import { accounts, sessions, users, verificationTokens } from "@/lib/db/schema";

const credentialsSchema = z.object({
  email: z.string().email(),
  password: z.string().min(8),
});

export const { handlers, signIn, signOut, auth } = NextAuth({
  // Adapter writes everything to our Drizzle tables — sessions, accounts,
  // verification tokens. Auth.js docs:
  // https://authjs.dev/getting-started/adapters/drizzle
  adapter: DrizzleAdapter(db, {
    usersTable: users,
    accountsTable: accounts,
    sessionsTable: sessions,
    verificationTokensTable: verificationTokens,
  }),
  // The Credentials provider REQUIRES the `jwt` strategy — Auth.js cannot
  // persist a credentials sign-in to the adapter's sessions table and throws
  // `UnsupportedStrategy` under `database`. Sessions are therefore stateless
  // JWTs; to force a global sign-out, rotate AUTH_SECRET.
  session: { strategy: "jwt", maxAge: 30 * 24 * 60 * 60 },
  pages: {
    signIn: "/signin",
    error: "/signin",
  },
  providers: [
    Credentials({
      name: "Email и пароль",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Пароль", type: "password" },
      },
      authorize: async (raw) => {
        const parsed = credentialsSchema.safeParse(raw);
        if (!parsed.success) return null;
        const { email, password } = parsed.data;
        const found = await db
          .select()
          .from(users)
          .where(eq(users.email, email.toLowerCase()))
          .limit(1);
        const user = found[0];
        if (!user?.passwordHash) {
          // No password = OAuth-only or non-existent. Same null response
          // either way so attackers can't enumerate accounts.
          return null;
        }
        const ok = await bcrypt.compare(password, user.passwordHash);
        if (!ok) return null;
        return {
          id: user.id,
          email: user.email,
          name: user.name,
          image: user.image,
        };
      },
    }),
  ],
  callbacks: {
    /** On sign-in (`user` is present) capture id + role into the JWT once, so
     *  later requests read them from the token instead of hitting the DB. */
    jwt: async ({ token, user }) => {
      if (user?.id) {
        token.id = user.id;
        const row = await db
          .select({ role: users.role })
          .from(users)
          .where(eq(users.id, user.id))
          .limit(1);
        token.role = row[0]?.role ?? "user";
      }
      return token;
    },
    /** Mirror id + role from the JWT onto the session so client-side
     *  `useSession()` and server-side `auth()` both see them. */
    session: async ({ session, token }) => {
      if (session.user) {
        session.user.id = (token.id as string) ?? session.user.id;
        session.user.role = (token.role as string) ?? "user";
      }
      return session;
    },
  },
});

// ─── Public-API password helpers (for signup route) ───────────────────────

/** Hash a plaintext password for storage in `users.passwordHash`.
 *  10 rounds = ~80 ms on modern hardware; balances UX and brute-force cost. */
export async function hashPassword(plain: string): Promise<string> {
  return bcrypt.hash(plain, 10);
}
